"""Teste da interface num navegador de verdade (Playwright + Chrome), com tests/dev_server.py num DATA_DIR novo.

    pip install playwright        (usa o Chrome instalado; ou rode "playwright install chromium")
    python tests/test_ui.py       (prints em tests/prints/)
"""
import os
import re
import sys

import httpx
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_dify import CHAVE_COMERCIAL, CHAVE_WORKFLOW  # noqa: E402

B = f"http://127.0.0.1:{os.getenv('APP_PORT', '8088')}"
PRINTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prints")
os.makedirs(PRINTS, exist_ok=True)
expect.set_options(timeout=8000)
falhas = 0


def ok(cond, msg):
    global falhas
    print(("  OK  " if cond else "  FALHA ") + msg)
    falhas += 0 if cond else 1


def checar(msg, fn):
    """Roda uma asserção do Playwright e registra OK/FALHA sem interromper o resto."""
    try:
        fn()
        ok(True, msg)
    except AssertionError as e:
        ok(False, f"{msg} -> {str(e).splitlines()[0][:160]}")


def entrar(page, usuario):
    page.goto(B + "/")
    page.fill("#login-usuario", usuario)
    page.fill("#login-senha", "Teste@123")
    page.click("#btn-entrar")
    expect(page.locator("#tela-app")).to_be_visible()


def perguntar(page, texto):
    page.fill("#pergunta", texto)
    page.click("#btn-enviar")


def print_(page, nome):
    page.screenshot(path=os.path.join(PRINTS, f"{nome}.png"), full_page=False)


with sync_playwright() as p:
    nav = p.chromium.launch(channel="chrome", headless=True)
    erros_console = []

    def novo(viewport=None):
        ctx = nav.new_context(viewport=viewport or {"width": 1280, "height": 800}, locale="pt-BR")
        pg = ctx.new_page()
        pg.on("console", lambda m: m.type == "error" and erros_console.append(f"{m.text} @ {pg.url}"))
        pg.on("pageerror", lambda e: erros_console.append(f"pageerror: {e} | {(e.stack or '')[:400]}"))
        return pg

    # ------------------------------------------------------------------ admin
    print("== admin: um agente só (vindo da migração)")
    adm = novo()
    entrar(adm, "admin.ti")
    checar("título mostra o agente PU", lambda: expect(adm.locator("#topo-titulo")).to_have_text("Agente Técnico PU(qlik)"))
    checar("seletor com 1 agente (travado)", lambda: expect(adm.locator("#sel-agente")).to_be_disabled())
    checar("atalhos de agente escondidos com 1 agente", lambda: expect(adm.locator("#agentes-rapidos")).to_be_hidden())
    checar("perguntas de exemplo do PU", lambda: expect(adm.locator(".sugestao").first).to_contain_text("Produto"))
    checar("placeholder com o nome do agente", lambda: expect(adm.locator("#pergunta")).to_have_attribute("placeholder", "Pergunte ao Agente Técnico PU(qlik)…"))
    print_(adm, "01-admin-chat-um-agente")

    print("== admin: cadastra agentes")
    adm.click("#btn-avatar")
    adm.click("#menu-admin")
    adm.click(".aba[data-aba=agentes]")
    checar("aba Agentes lista o PU", lambda: expect(adm.locator(".agente-linha")).to_have_count(1))
    checar("chave aparece mascarada", lambda: expect(adm.locator(".agente-linha .mono")).to_have_text(re.compile(r"^app-PUq…\w{4}$")))
    adm.click("#btn-add-agente")
    adm.click("#agente-salvar")
    checar("sem chave: erro no diálogo", lambda: expect(adm.locator("#agente-erro")).to_contain_text("Cole a chave"))
    adm.fill("#agente-chave", CHAVE_WORKFLOW)
    adm.click("#agente-salvar")
    checar("workflow recusado com explicação", lambda: expect(adm.locator("#agente-erro")).to_contain_text("workflow"))
    print_(adm, "02-dialogo-agente-erro")
    adm.fill("#agente-chave", CHAVE_COMERCIAL)
    adm.click("#agente-salvar")
    checar("comercial cadastrado (diálogo fecha)", lambda: expect(adm.locator("#dlg-agente")).to_be_hidden())
    checar("lista com 2 agentes", lambda: expect(adm.locator(".agente-linha")).to_have_count(2))
    checar("ícone emoji do Dify", lambda: expect(adm.locator(".agente-linha", has_text="Agente Comercial").locator(".agente-icone")).to_have_text("💼"))
    checar("tipo do app (Chatflow)", lambda: expect(adm.locator(".agente-linha", has_text="Agente Comercial").locator(".selo-modo")).to_have_text("Chatflow"))
    print_(adm, "03-aba-agentes")

    print("== admin: libera maria só no comercial")
    adm.click(".aba[data-aba=usuarios]")
    checar("admin com todos os agentes", lambda: expect(adm.locator(".usuario .btn-agentes").first).to_have_text("Todos os agentes (2)"))
    adm.click("#btn-add-usuario")
    checar("diálogo lista os 2 agentes", lambda: expect(adm.locator("#agentes-novo .check-agente")).to_have_count(2))
    adm.select_option("#perfil-novo", "admin")
    checar("perfil admin marca e trava todos", lambda: expect(adm.locator("#agentes-novo input:checked:disabled")).to_have_count(2))
    adm.select_option("#perfil-novo", "usuario")
    for cb in adm.locator("#agentes-novo input").all():
        cb.uncheck()
    adm.locator("#agentes-novo .check-agente", has_text="Agente Comercial").locator("input").check()
    adm.fill("#busca-ad", "maria")
    adm.locator(".res-ad", has_text="Maria Silva").get_by_role("button", name="Liberar acesso").click()
    checar("maria liberada", lambda: expect(adm.locator(".res-ad", has_text="Maria Silva").get_by_role("button")).to_have_text("Liberado ✓"))
    print_(adm, "04-dialogo-usuario")
    adm.keyboard.press("Escape")
    linha_maria = adm.locator(".usuario", has_text="maria.silva")
    checar("linha da maria mostra o comercial", lambda: expect(linha_maria.locator(".btn-agentes")).to_have_text("Agente Comercial"))

    print("== admin: troca de agente no chat")
    adm.click("#btn-avatar")
    adm.click("#menu-chat")
    checar("seletor liberado com 2 agentes", lambda: expect(adm.locator("#sel-agente")).to_be_enabled())
    checar("atalhos de agente na tela inicial", lambda: expect(adm.locator(".agente-rapido")).to_have_count(2))
    print_(adm, "05-chat-dois-agentes")
    adm.locator(".agente-rapido", has_text="Agente Comercial").click()
    checar("título troca para o comercial", lambda: expect(adm.locator("#topo-titulo")).to_have_text("Agente Comercial"))
    checar("sugestões do Dify do comercial", lambda: expect(adm.locator(".sugestao")).to_have_text(["Quais espumas temos para colchões?"]))
    checar("descrição do Dify como abertura", lambda: expect(adm.locator("#abertura")).to_contain_text("sem acesso ao ERP"))
    perguntar(adm, "Quais linhas para colchão?")
    checar("resposta do comercial", lambda: expect(adm.locator(".msg-bot .md").last).to_contain_text("Não tenho acesso ao ERP"))
    checar("conversa listada no comercial", lambda: expect(adm.locator(".conversa")).to_have_count(1))
    checar("URL com agente e conversa", lambda: expect(adm).to_have_url(re.compile(r"/\?a=\d+&c=[0-9a-f-]+$")))
    adm.select_option("#sel-agente", label="Agente Técnico PU(qlik)")
    checar("PU tem histórico próprio (vazio)", lambda: expect(adm.locator(".conversa")).to_have_count(0))
    checar("volta para a tela inicial", lambda: expect(adm.locator("#boas-vindas")).to_be_visible())

    print("== admin: trocar de agente no meio da resposta")
    perguntar(adm, "Tudo sobre o FLEXX AG 2032")
    checar("status da ferramenta aparece", lambda: expect(adm.locator(".status").last).to_contain_text("Qlik"))
    adm.select_option("#sel-agente", label="Agente Comercial")
    adm.wait_for_timeout(2500)  # tempo de a resposta do PU terminar no servidor
    checar("comercial continua com 1 conversa", lambda: expect(adm.locator(".conversa")).to_have_count(1))
    checar("resposta do PU não invade o comercial", lambda: expect(adm.locator("#mensagens")).not_to_contain_text("FLEXX AG 2032 — visão geral"))
    checar("nenhuma conversa marcada", lambda: expect(adm.locator(".conversa.ativa")).to_have_count(0))
    checar("botão enviar volta ao normal", lambda: expect(adm.locator("#btn-parar")).to_be_hidden())
    adm.select_option("#sel-agente", label="Agente Técnico PU(qlik)")
    checar("conversa do PU foi salva no PU", lambda: expect(adm.locator(".conversa")).to_have_count(1))

    print("== admin: edita o agente")
    adm.goto(B + "/admin")
    adm.click(".aba[data-aba=agentes]")
    adm.locator(".agente-linha", has_text="Agente Técnico PU").locator(".ed").click()
    checar("sugestões no formato Categoria | pergunta",
           lambda: expect(adm.locator("#agente-sugestoes")).to_have_value(re.compile(r"^Produto \| Tudo sobre o FLEXX AG 2032")))
    adm.fill("#agente-nome", "PU completo")
    adm.fill("#agente-sugestoes", "Custos | Custo do FLEXX TH\nPergunta sem categoria")
    adm.click("#agente-salvar")
    checar("lista mostra o novo nome", lambda: expect(adm.locator(".agente-linha", has_text="PU completo")).to_have_count(1))
    adm.goto(B + "/")
    adm.select_option("#sel-agente", label="PU completo")
    checar("chat usa o novo nome", lambda: expect(adm.locator("#topo-titulo")).to_have_text("PU completo"))
    checar("sugestões novas", lambda: expect(adm.locator(".sugestao")).to_have_text(["CustosCusto do FLEXX TH", "Pergunta sem categoria"]))

    # ------------------------------------------------------------------ maria
    print("== maria: só o comercial")
    maria = novo()
    entrar(maria, "maria.silva")
    checar("maria abre direto no comercial", lambda: expect(maria.locator("#topo-titulo")).to_have_text("Agente Comercial"))
    checar("seletor com uma opção", lambda: expect(maria.locator("#sel-agente option")).to_have_count(1))
    checar("sem menu de administração", lambda: expect(maria.locator("#menu-admin")).to_be_hidden())
    perguntar(maria, "Oi")
    checar("maria conversa com o comercial", lambda: expect(maria.locator(".msg-bot .md").last).to_contain_text("Agente Comercial"))
    maria.goto(B + "/?a=1")
    checar("URL com agente sem acesso cai no permitido", lambda: expect(maria.locator("#topo-titulo")).to_have_text("Agente Comercial"))

    print("== maria: perde o acesso com a tela aberta")
    api_adm = httpx.Client(base_url=B, headers={"X-Requested-With": "agente-pu"})
    api_adm.post("/api/auth/login", json={"usuario": "admin.ti", "senha": "Teste@123"})
    api_adm.put("/api/admin/usuarios/maria.silva/agentes", json={"agentes": []})
    perguntar(maria, "Mais uma pergunta")
    checar("aviso de acesso removido", lambda: expect(maria.locator("#toast")).to_contain_text("não tem acesso"))
    checar("tela de nenhum agente liberado", lambda: expect(maria.locator("#abertura")).to_contain_text("Nenhum agente foi liberado"))
    checar("caixa de pergunta desativada", lambda: expect(maria.locator("#pergunta")).to_be_disabled())
    checar("seletor some", lambda: expect(maria.locator("#seletor-agente")).to_be_hidden())
    checar("nova conversa some", lambda: expect(maria.locator("#btn-nova")).to_be_hidden())
    print_(maria, "06-maria-sem-agente")

    print("== celular")
    cel = novo({"width": 390, "height": 844})
    entrar(cel, "admin.ti")
    checar("atalhos de agente visíveis no celular", lambda: expect(cel.locator(".agente-rapido")).to_have_count(2))
    print_(cel, "07-celular-chat")
    cel.click("#btn-menu")
    checar("seletor na gaveta", lambda: expect(cel.locator("#sel-agente")).to_be_visible())
    cel.wait_for_timeout(400)  # fim da animação da gaveta
    print_(cel, "08-celular-gaveta")
    cel.goto(B + "/admin")
    checar("lista de usuários no celular", lambda: expect(cel.locator(".usuario .btn-agentes").first).to_be_visible())
    print_(cel, "09-celular-admin-usuarios")
    cel.click(".aba[data-aba=agentes]")
    checar("lista de agentes no celular", lambda: expect(cel.locator(".agente-linha")).to_have_count(2))
    print_(cel, "10-celular-admin-agentes")

    escuro = novo()
    escuro.emulate_media(color_scheme="dark")
    entrar(escuro, "admin.ti")
    print_(escuro, "11-escuro-chat")

    # respostas 400/401/403 esperadas (login, chave recusada, acesso tirado) aparecem como "Failed to load resource"
    reais = [e for e in erros_console if not re.search(r"Failed to load resource: .* (400|401|403) ", e)]
    ok(not reais, f"sem erros de JavaScript no console {reais[:3]}")
    nav.close()

print("\nfalhas:", falhas)
sys.exit(1 if falhas else 0)
