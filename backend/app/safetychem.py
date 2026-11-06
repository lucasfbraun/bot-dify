"""FISPQs oficiais do SafetyChem (Intertox): importação do XML exportado e ferramenta para o agente.

O SafetyChem é a fonte oficial e atualizada das FISPQs/FDS e fichas de emergência. Quando um produto existe
aqui, ele PREVALECE sobre as FISPQs antigas da pasta de documentos e do catálogo.

- O admin exporta os produtos em XML (SafetyChem > Dados > Exportar, formato "XML") e importa na plataforma.
  Reimportar atualiza os produtos alterados e inclui os novos, sem duplicar (chave = nome comercial).
- SEGREDO INDUSTRIAL: os ingredientes marcados como segredo industrial (nome, CAS e concentração reais) são
  descartados na importação e nunca ficam guardados. A composição que fica é a da Seção 3 publicada na FISPQ.
- O agente consulta pela ferramenta GET /api/ferramentas/fds-safetychem (mesma chave DOCUMENTOS_CHAVE).
"""
import json
import logging
import re
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, Request

from . import db, documentos
from .security import exigir_admin

log = logging.getLogger("agente.safetychem")
router = APIRouter()

MAX_MB = 30
MAX_RESULTADOS = 5

# códigos de pictograma do SafetyChem (manual de exportação, seção 6)
PICTOGRAMAS = {
    "01": "Explosivo (bomba explodindo)", "03": "Inflamável (chama)", "04": "Oxidante (chama sobre círculo)",
    "05": "Gás sob pressão (cilindro)", "06": "Corrosivo", "07": "Toxicidade aguda (caveira)",
    "08": "Irritante / nocivo (ponto de exclamação)", "09": "Perigo à saúde (silhueta)",
    "10": "Perigoso ao meio ambiente",
}
# rótulos de risco do transporte (manual, seção 7)
ROTULOS_TRANSPORTE = {
    "rr1": "Classe 1 - Explosivos", "rr11": "Subclasse 1.1 - Explosivos", "rr12": "Subclasse 1.2 - Explosivos",
    "rr13": "Subclasse 1.3 - Explosivos", "rr14": "Subclasse 1.4 - Explosivos", "rr15": "Subclasse 1.5 - Explosivos",
    "rr16": "Subclasse 1.6 - Explosivos", "rr21": "Classe 2.1 - Gás inflamável",
    "rr22": "Classe 2.2 - Gás não inflamável e não tóxico", "rr23": "Classe 2.3 - Gás tóxico",
    "rr3": "Classe 3 - Líquido inflamável", "rr41": "Classe 4.1 - Sólido inflamável",
    "rr42": "Classe 4.2 - Sujeito a combustão espontânea", "rr43": "Classe 4.3 - Perigoso quando molhado",
    "rr51": "Classe 5.1 - Oxidante", "rr52": "Classe 5.2 - Peróxido orgânico", "rr61": "Classe 6.1 - Tóxico",
    "rr62": "Classe 6.2 - Infectante", "rr8": "Classe 8 - Corrosivo", "rr9": "Classe 9 - Perigos diversos",
    "rrpm": "Perigoso ao meio ambiente",
}


# ------------------------------------------------------------------ leitura do XML
def limpar(texto: str | None) -> str:
    """Tira a marcação do SafetyChem (\\b negrito\\b0, \\super 1\\super0, \\line) e espaços sobrando."""
    t = texto or ""
    t = re.sub(r"\\super\s*(\S+?)\s*\\super0", r"(\1)", t)
    t = re.sub(r"\\line\b", "\n", t)
    t = re.sub(r"\\[a-z]+0?\b ?", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _txt(prod: ET.Element, tag: str) -> str:
    el = prod.find(tag)
    return limpar(el.text) if el is not None else ""


def _todos(prod: ET.Element, tag: str) -> list[str]:
    """Campos que aparecem repetidos (ex.: URL_FISPQ da FISPQ e da ficha de emergência)."""
    return [limpar(el.text) for el in prod.findall(tag)]


def _lista(prod: ET.Element, tag: str) -> list[str]:
    """<Tag><Tag>a</Tag><Tag>b</Tag></Tag> -> ["a", "b"]"""
    el = prod.find(tag)
    if el is None:
        return []
    return [v for v in (limpar(f.text) for f in el.findall(tag)) if v]


def _frases(texto: str) -> list[str]:
    """Frases separadas por "#", sem o cabeçalho da seção ("PREVENÇÃO:")."""
    return [f.strip() for f in (texto or "").split("#") if f.strip() and not re.fullmatch(r"[A-ZÀ-Ú ]+:", f.strip())]


def _numero(texto: str) -> str:
    """"1230,00000" -> "1230"; "0,95000" -> "0,95"."""
    t = (texto or "").strip()
    if re.fullmatch(r"-?\d+(,\d+)?", t):
        inteiro, _, dec = t.partition(",")
        dec = dec.rstrip("0")
        return f"{inteiro},{dec}" if dec else inteiro
    return t


def _faixa(prod, prefixo: str, unidade: str = "", temp: str = "") -> str:
    sinal = _txt(prod, f"ExportarDados_{prefixo}Sinal")
    ini, fim = _numero(_txt(prod, f"ExportarDados_{prefixo}ValorInicial")), _numero(_txt(prod, f"ExportarDados_{prefixo}ValorFinal"))
    if not ini:
        return ""
    valor = f"{sinal} {ini}".strip() + (f" a {fim}" if fim else "")
    un = _txt(prod, f"ExportarDados_{prefixo}Unidade") if not unidade else unidade
    t = _numero(_txt(prod, f"ExportarDados_{prefixo}TemperaturaInicial")) if temp else ""
    return f"{valor} {un}".strip() + (f" a {t} °C" if t else "")


def produto_do_xml(prod: ET.Element) -> dict | None:
    nome = _txt(prod, "Nome_comercial")
    if not nome:
        return None
    urls = [u for u in _todos(prod, "URL_FISPQ") if u.startswith("https://")]
    # ingredientes: só os que NÃO são segredo industrial (os secretos são descartados aqui e nunca guardados)
    ingredientes = []
    for ing in prod.findall("Ingredientes/Ingrediente"):
        if _txt(ing, "Composicao_-_Segredo_Industrial").lower() != "false":
            continue
        ingredientes.append({"nome": _txt(ing, "Composicao_-_Nome"), "cas": _txt(ing, "Composicao_-_CAS"),
                             "faixa": _txt(ing, "Composicao_-_Descricao_Concentracao")})
    hommel = _lista(prod, "Hommel")
    onu = _txt(prod, "Numero_ONU")
    return {
        "nome": nome,
        "versao": _txt(prod, "Versao"),
        "codigo": _txt(prod, "Codigo_da_empresa"),
        "nome_quimico": _txt(prod, "Descricao_do_Produto_-_Nome_Quimico"),
        "uso_recomendado": _txt(prod, "Principal_uso_recomendado"),
        "mistura": _txt(prod, "Mistura") == "True",
        "produto_perigoso": _txt(prod, "Produto_Perigoso") == "True",
        "classificacao": {
            "palavra_advertencia": _txt(prod, "Palavras_de_Advertencia"),
            "pictogramas": [f"{c} - {PICTOGRAMAS.get(c, 'código ' + c)}" for c in _lista(prod, "Pictogramas_-_GHS")],
            "frases_perigo": _lista(prod, "Frases_de_Perigo"),
            "sintomas_e_efeitos": _txt(prod, "Sintomas_e_efeitos_mais_importantes__agudos_e_tardios"),
        },
        "precaucoes": {
            "geral": _frases(_txt(prod, "Precaucao_Geral")),
            "prevencao": _frases(_txt(prod, "Prevencao")),
            "resposta_emergencia": _frases(_txt(prod, "Resposta_a_Emergencia")),
            "armazenamento": _frases(_txt(prod, "Armazenamento")),
            "disposicao": _frases(_txt(prod, "Disposicao")),
        },
        "composicao_secao3_publicada": _txt(prod, "Composicao_-_Secao_3_FISPQ"),
        "ingredientes_publicos": ingredientes,
        "cas_produto": _txt(prod, "CAS"),
        "primeiros_socorros": {
            "inalacao": _txt(prod, "Inalacao"), "contato_pele": _txt(prod, "ContatoComAPele"),
            "contato_olhos": _txt(prod, "ContatoComOsOlhos"), "ingestao": _txt(prod, "Ingestao"),
            "notas_medico": _txt(prod, "Documentos_NotasparaMedico") or _txt(prod, "Informacões_ao_Medico"),
        },
        "combate_incendio": {
            "meios_extincao": _txt(prod, "Fogo_Acidente"),
            "protecao_bombeiros": _txt(prod, "NBR14725MedidasDeProtecaoEquipeCombateIncendio"),
            "prevencao": _txt(prod, "Prevencao_de_incencio_e_explosao"),
        },
        "vazamento": {
            "pessoal_nao_emergencia": _txt(prod, "ParaOPessoalQueNaoFazParteDosServicoesDeEmergencia"),
            "pessoal_emergencia": _txt(prod, "Para_o_pessoal_de_servico_de_emergencia"),
            "meio_ambiente": _txt(prod, "Precaucões_ao_meio_ambiente"),
            "contencao_limpeza": _txt(prod, "MetodoEMateriaisParaContencaoELimpeza"),
        },
        "manuseio_armazenamento": {
            "manuseio_seguro": _txt(prod, "Precaucões_para_manuseio_seguro"),
            "incompativel_com": _txt(prod, "Incompativel_com"),
            "embalagem_usada": _txt(prod, "Embalagem_usada"),
        },
        "epi": _lista(prod, "Composicao_-_EPI"),
        "propriedades": {
            "aspecto": _txt(prod, "Aspecto"),
            "solubilidade_agua": _txt(prod, "Solubilidade_em_agua"),
            "densidade_absoluta": _faixa(prod, "DensidadeAbsoluta", temp="s"),
            "densidade_relativa": _faixa(prod, "DensidadeRelativa", unidade=" "),
            "ponto_de_fulgor": _faixa(prod, "PontoDeFulgor", unidade="°C"),
            "estabilidade": _txt(prod, "ExportarDados_EstabilidadeDescricao") or _txt(prod, "Estabilidade"),
        },
        "diagrama_hommel": ({"saude": hommel[0], "inflamabilidade": hommel[1], "reatividade": hommel[2],
                             "risco_especifico": hommel[3] if len(hommel) > 3 else ""} if len(hommel) >= 3 else {}),
        "transporte": {
            "numero_onu": "" if onu in ("", "0") else onu,
            "classificado_para_transporte": onu not in ("", "0"),
            "nome_apropriado_embarque": _txt(prod, "Nome_apropriado_para_embarque"),
            "classe_risco": _txt(prod, "Classe_ou_subclasse_de_risco"),
            "descricao_classe": _txt(prod, "Descricao_da_classe_ou_subclasse_de_risco"),
            "numero_risco": _txt(prod, "Numero_de_risco"),
            "grupo_embalagem": _txt(prod, "Grupo_de_embalagem"),
            "rotulos_risco": [ROTULOS_TRANSPORTE.get(c.lower(), c) for c in _lista(prod, "Pictogramas_-__Transporte")],
        },
        "ficha_emergencia": {
            "aspecto": _txt(prod, "Aspecto"), "epi": _frases(_txt(prod, "EPI")),
            "riscos_fogo": _txt(prod, "Fogo"), "riscos_saude": _txt(prod, "Saude"),
            "riscos_meio_ambiente": _txt(prod, "Meio_Ambiente"), "vazamento": _txt(prod, "Vazamento"),
            "fogo": _txt(prod, "Fogo_Acidente"), "poluicao": _txt(prod, "Poluicao"),
            "envolvimento_pessoas": _txt(prod, "Envolvimento_de_pessoas"),
            "informacoes_medico": _txt(prod, "Informacões_ao_Medico"), "observacoes": _txt(prod, "Observacões"),
        },
        "url_fispq": urls[0] if urls else "",
        "url_ficha_emergencia": urls[1] if len(urls) > 1 else "",
    }


def ler_xml(dados: bytes) -> list[dict]:
    cabeca = dados[:4096].lower()
    if b"<!doctype" in cabeca or b"<!entity" in cabeca:
        raise HTTPException(400, "XML recusado: declarações DOCTYPE/ENTITY não são aceitas.")
    try:
        raiz = ET.fromstring(dados)
    except ET.ParseError as e:
        raise HTTPException(400, f"O arquivo não é um XML válido ({e}).")
    if raiz.tag != "Produtos":
        raise HTTPException(400, "Este XML não é uma exportação do SafetyChem (falta <Produtos>). "
                                 "Exporte em SafetyChem > Dados > Exportar, formato \"XML\".")
    produtos = [p for p in (produto_do_xml(el) for el in raiz.findall("Produto")) if p]
    if not produtos:
        raise HTTPException(400, "Nenhum produto com nome comercial no XML. Confira se o campo \"Nome comercial\" "
                                 "estava marcado na exportação.")
    return produtos


# ------------------------------------------------------------------ banco
def _chave(nome: str) -> str:
    return documentos.normalizar(nome)


def gravar(produtos: list[dict], autor: str) -> dict:
    novos = atualizados = iguais = 0
    agora = db.agora()
    with db.conn() as c:
        for p in produtos:
            chave = _chave(p["nome"])
            dados = json.dumps(p, ensure_ascii=False, sort_keys=True)
            atual = c.execute("SELECT dados FROM safetychem WHERE chave=?", (chave,)).fetchone()
            if atual and atual["dados"] == dados:
                iguais += 1
                continue
            c.execute("INSERT INTO safetychem (chave, nome, versao, dados, importado_em, importado_por) "
                      "VALUES (?,?,?,?,?,?) ON CONFLICT(chave) DO UPDATE SET nome=excluded.nome, "
                      "versao=excluded.versao, dados=excluded.dados, importado_em=excluded.importado_em, "
                      "importado_por=excluded.importado_por",
                      (chave, p["nome"], p["versao"], dados, agora, autor))
            if atual:
                atualizados += 1
            else:
                novos += 1
    return {"total": len(produtos), "novos": novos, "atualizados": atualizados, "sem_alteracao": iguais}


def todos() -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT chave, nome, versao, dados, importado_em FROM safetychem ORDER BY nome")]


def _itens_busca() -> list[dict]:
    """No formato que documentos.buscar entende (a busca por nome é a mesma dos documentos da pasta)."""
    out = []
    for r in todos():
        p = json.loads(r["dados"])
        out.append({"produto": r["nome"], "tipo": "FISPQ", "formato": "pdf", "arquivo": r["nome"],
                    "_busca_produto": r["chave"], "_busca_arquivo": documentos.normalizar(p.get("nome_quimico", "")),
                    "_dados": p, "_importado_em": r["importado_em"]})
    return out


def buscar(produto: str) -> list[dict]:
    return documentos.buscar(_itens_busca(), produto)[:MAX_RESULTADOS]


def _rotulo_fispq(p: dict) -> str:
    # o SafetyChem exporta o modelo novo (FDS, ABNT NBR 14725:2023): "FISPQ" no título faria o agente tratá-lo
    # como documento antigo
    return f"FDS {p['nome']} (SafetyChem" + (f", versão {p['versao']}" if p.get("versao") else "") + ")"


def documentos_oficiais(produto: str, tipo: str = "") -> list[dict]:
    """FISPQ e ficha de emergência oficiais (links do SafetyChem) para a ferramenta de documentos."""
    tipo_n = documentos.normalizar(tipo)
    out = []
    for i in buscar(produto):
        p = i["_dados"]
        # (tipo da pasta que ele substitui, palavras aceitas no filtro, link, texto)
        candidatos = [("FISPQ", "fispq fds sds", p.get("url_fispq"), _rotulo_fispq(p)),
                      ("Ficha de emergência", "ficha de emergencia", p.get("url_ficha_emergencia"),
                       f"Ficha de emergência {p['nome']} (SafetyChem)")]
        for rotulo_tipo, aceitos, url, texto in candidatos:
            if not url or (tipo_n and tipo_n not in aceitos):
                continue
            out.append({"produto": p["nome"], "familia": "SafetyChem (oficial)", "tipo": rotulo_tipo,
                        "arquivo": texto, "formato": "PDF", "link": url, "fonte": "SafetyChem (oficial, atualizada)",
                        "link_markdown": f"[📄 {texto}]({url})", "_chave": i["_busca_produto"]})
    return out


# ------------------------------------------------------------------ rotas: administração
@router.post("/api/admin/safetychem/importar")
async def importar(request: Request, admin: dict = Depends(exigir_admin)):
    limite = MAX_MB * 1024 * 1024
    dados = bytearray()
    async for parte in request.stream():
        dados += parte
        if len(dados) > limite:
            raise HTTPException(413, f"O arquivo passou de {MAX_MB} MB. Exporte em partes (por linha de produto).")
    if not dados:
        raise HTTPException(400, "Arquivo vazio.")
    produtos = ler_xml(bytes(dados))
    r = gravar(produtos, admin["username"])
    db.set_meta("safetychem_ultima_importacao", json.dumps({**r, "quando": db.agora(), "por": admin["username"]}))
    db.auditar(admin["username"], "importar_safetychem", "", f"{r['total']} produtos: {r['novos']} novos, "
               f"{r['atualizados']} atualizados, {r['sem_alteracao']} sem alteração")
    log.info("SafetyChem importado por %s: %s", admin["username"], r)
    return r


@router.get("/api/admin/safetychem")
def situacao(admin: dict = Depends(exigir_admin)):
    itens = todos()
    ultima = db.get_meta("safetychem_ultima_importacao")
    return {"produtos": len(itens), "ultima_importacao": json.loads(ultima) if ultima else None,
            "itens": [{"nome": r["nome"], "versao": r["versao"], "importado_em": r["importado_em"],
                       "tem_link": bool(json.loads(r["dados"]).get("url_fispq"))} for r in itens]}


# ------------------------------------------------------------------ ferramenta para o agente (Dify)
@router.get("/api/ferramentas/fds-safetychem")
async def ferramenta_fispq(request: Request, produto: str = ""):
    """Dados da FISPQ/FDS oficial e atualizada do produto (SafetyChem). Prevalece sobre FISPQs antigas."""
    documentos._checar_chave(request)
    produto = produto.strip()[:120]
    if not documentos.normalizar(produto):
        raise HTTPException(400, "Informe o produto (ex.: AG 2032).")
    achados = buscar(produto)
    if not achados:
        sugestoes = documentos.parecidos(_itens_busca(), produto)
        return {"encontrados": 0, "produtos": [], "produtos_parecidos": sugestoes,
                "orientacao": "Este produto não está no SafetyChem. Use a FISPQ/FDS do catálogo, avisando que pode "
                              "estar desatualizada." + (" Ou pergunte se o usuário quis dizer um destes produtos."
                                                  if sugestoes else "")}
    produtos = []
    for i in achados:
        p = dict(i["_dados"])
        p["importado_em"] = i["_importado_em"]
        if p.get("url_fispq"):
            p["link_markdown_fispq"] = f"[📄 {_rotulo_fispq(p)}]({p['url_fispq']})"
        produtos.append(p)
    return {"encontrados": len(produtos), "produtos": produtos,
            "orientacao": "FDS OFICIAL e atualizada (SafetyChem, modelo ABNT NBR 14725:2023). Prevalece sobre as "
                          "FISPQs/FDS antigas do catálogo e da pasta: em caso de diferença, use esta e avise que o "
                          "documento antigo está desatualizado. Não vale para boletins técnicos (aplicação e "
                          "propriedades de uso continuam vindo do boletim). Cite \"FDS SafetyChem, versão <versao>\". "
                          "Para o usuário abrir o "
                          "documento, use o link_markdown_fispq exatamente como veio."}
