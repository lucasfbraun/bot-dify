"""FISPQs oficiais do SafetyChem (Intertox): importação do XML exportado e ferramenta para o agente.

O SafetyChem é a fonte oficial e atualizada das FISPQs/FDS e fichas de emergência. Quando um produto existe
aqui, ele PREVALECE sobre as FISPQs antigas da pasta de documentos e do catálogo.

- O admin exporta os produtos em XML (SafetyChem > Dados > Exportar, formato "XML") e importa na plataforma.
  Reimportar atualiza os produtos alterados e inclui os novos, sem duplicar (chave = nome comercial).
- SEGREDO INDUSTRIAL: os ingredientes marcados como segredo industrial (nome, CAS e concentração reais) são
  descartados na importação e nunca ficam guardados. A composição que fica é a da Seção 3 publicada na FISPQ.
- O agente consulta pela ferramenta dados_oficiais_safetychem (GET /api/ferramentas/safetychem, mesma chave
  DOCUMENTOS_CHAVE), que separa a resposta por documento: ficha técnica, FDS (antiga FISPQ) e ficha de emergência.
- Para achar produto PARECIDO (ex.: com o boletim de um concorrente), buscar_fichas_safetychem
  (GET /api/ferramentas/safetychem/buscar) procura pelo conteúdo das fichas técnicas: aplicação, tipo e propriedades.
"""
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import secrets
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote, unquote

import httpx

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from . import conhecimento, db, documentos, tempo
from .config import settings
from .security import exigir_admin, usuario_atual

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
    """Produtos com qualquer documento do SafetyChem (XML da FDS, ficha técnica, FDS ou SDS em PDF), no formato
    que documentos.buscar entende."""
    xml = {r["chave"]: r for r in todos()}
    fichas = {r["chave"]: r for r in fichas_todas()}
    fds = {r["chave"]: r for r in docs_todos("fds")}
    sds = {r["chave"]: r for r in docs_todos("sds")}
    out = []
    for chave in sorted(set(xml) | set(fichas) | set(fds) | set(sds)):
        f = xml.get(chave)
        algum = f or fichas.get(chave) or fds.get(chave) or sds.get(chave)
        p = json.loads(f["dados"]) if f else {"nome": algum["nome"]}
        out.append({"produto": p["nome"], "tipo": "FISPQ", "formato": "pdf", "arquivo": p["nome"],
                    "_busca_produto": chave, "_busca_arquivo": documentos.normalizar(p.get("nome_quimico", "")),
                    "_dados": p, "_ficha": fichas.get(chave), "_fds": fds.get(chave), "_sds": sds.get(chave),
                    "_importado_em": algum["importado_em"]})
    return out


def buscar(produto: str) -> list[dict]:
    return documentos.buscar(_itens_busca(), produto)[:MAX_RESULTADOS]


def _rotulo_fispq(p: dict) -> str:
    # o SafetyChem exporta o modelo novo (FDS, ABNT NBR 14725:2023): "FISPQ" no título faria o agente tratá-lo
    # como documento antigo
    return f"FDS {p['nome']} (SafetyChem" + (f", versão {p['versao']}" if p.get("versao") else "") + ")"


def documentos_oficiais(produto: str, tipo: str = "") -> list[dict]:
    """FISPQ e ficha de emergência oficiais (links do SafetyChem) para a ferramenta de documentos."""
    tipo_n = documentos.tipo_canonico(tipo)
    out = []
    for i in buscar(produto):
        p, t, f, sd = i["_dados"], i["_ficha"], i["_fds"], i["_sds"]
        # FDS: o PDF publicado (link oficial) ou, sem ele, o link que veio no XML
        url_fds = _link_pdf_seguranca(f) if f else p.get("url_fispq")
        rotulo_fds = _rotulo_seguranca("fds", f) if f else _rotulo_fispq(p)
        # (tipo da pasta que ele substitui, palavras aceitas no filtro, link, texto)
        candidatos = [("Boletim técnico", "boletim", _link_ficha(t) if t else "", _rotulo_ficha(t) if t else ""),
                      ("FISPQ", "fispq", url_fds, rotulo_fds),
                      ("SDS (inglês)", "sds", _link_pdf_seguranca(sd) if sd else "", _rotulo_seguranca("sds", sd) if sd else ""),
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
def admin_ou_robo(request: Request) -> dict:
    """O robô (RPA) se identifica pela chave SAFETYCHEM_ROBO_CHAVE; senão, precisa ser admin logado."""
    chave = settings.SAFETYCHEM_ROBO_CHAVE
    auth = request.headers.get("authorization", "")
    if chave and auth.lower().startswith("bearer ") and secrets.compare_digest(auth[7:].strip(), chave):
        return {"username": "robo-safetychem", "perfil": "admin"}
    return exigir_admin(usuario_atual(request))


@router.post("/api/admin/safetychem/importar")
async def importar(request: Request, admin: dict = Depends(admin_ou_robo)):
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
    fichas = fichas_todas()
    fds_pdf, sds = docs_todos("fds"), docs_todos("sds")
    ultima = db.get_meta("safetychem_ultima_importacao")
    ultima_ficha = db.get_meta("safetychem_ultima_ficha")
    return {"produtos": len(itens), "ultima_importacao": json.loads(ultima) if ultima else None,
            "fichas": len(fichas), "ultima_ficha": json.loads(ultima_ficha) if ultima_ficha else None,
            "fds_pdf": len(fds_pdf), "sds": len(sds),
            "itens": [{"nome": r["nome"], "versao": r["versao"], "importado_em": r["importado_em"],
                       "tem_link": bool(json.loads(r["dados"]).get("url_fispq"))} for r in itens],
            "itens_fichas": [{"nome": r["nome"], "versao": r["versao"], "data_versao": r["data_versao"],
                              "arquivo": r["arquivo_nome"], "importado_em": r["importado_em"]} for r in fichas]}


# ------------------------------------------------------------------ ferramenta para o agente (Dify)
# campos guardados de cada documento (a importação guarda tudo junto; a ferramenta separa por documento)
CAMPOS_FDS = ("uso_recomendado", "mistura", "produto_perigoso", "cas_produto", "classificacao", "precaucoes",
              "composicao_secao3_publicada", "ingredientes_publicos", "primeiros_socorros", "combate_incendio",
              "vazamento", "manuseio_armazenamento", "epi", "propriedades", "diagrama_hommel", "transporte")
DOCUMENTOS = {"boletim": "ficha_tecnica", "fispq": "fds", "sds": "sds", "ficha de emergencia": "ficha_emergencia"}


def _por_documento(p: dict, importado_em: str, documento: str, ficha: dict | None = None,
                   fds_pdf: dict | None = None, sds: dict | None = None) -> dict:
    fds = {"versao": p.get("versao", ""), **{k: p[k] for k in CAMPOS_FDS if k in p}} if "classificacao" in p else {}
    if fds and p.get("url_fispq"):
        fds["link_markdown"] = f"[📄 {_rotulo_fispq(p)}]({p['url_fispq']})"
    if fds_pdf:  # a FDS publicada (PDF) traz as 16 seções; o XML, quando houver, completa com os campos separados
        fds.update(_seguranca_publica("fds", fds_pdf))
    fe = dict(p.get("ficha_emergencia") or {})
    if fe:
        fe["transporte"] = p.get("transporte", {})
        if p.get("url_ficha_emergencia"):
            fe["link_markdown"] = f"[📄 Ficha de emergência {p['nome']} (SafetyChem)]({p['url_ficha_emergencia']})"
    docs = {"ficha_tecnica": _ficha_publica(ficha) if ficha else None, "fds": fds or None,
            "ficha_emergencia": fe or None, "sds": _seguranca_publica("sds", sds) if sds else None}
    out = {"nome": p["nome"], "nome_quimico": p.get("nome_quimico", ""), "importado_em": importado_em,
           "documentos_disponiveis": [k for k, v in docs.items() if v]}
    pedido = DOCUMENTOS.get(documentos.tipo_canonico(documento)) if documento else None
    for k, v in docs.items():
        if pedido is None or k == pedido:
            out[k] = v
    if out.get("ficha_tecnica", 1) is None:
        out["aviso_ficha_tecnica"] = ("A ficha técnica deste produto ainda não foi importada do SafetyChem: use o "
                                      "boletim técnico do catálogo/pasta.")
    if out.get("fds", 1) is None:
        out["aviso_fds"] = "A FDS deste produto ainda não foi importada do SafetyChem: use a FISPQ/FDS do catálogo."
    if out.get("sds", 1) is None:
        out["aviso_sds"] = "Não há SDS (versão em inglês) deste produto no SafetyChem."
    return out


@router.get("/api/ferramentas/safetychem")
async def ferramenta_safetychem(request: Request, produto: str = "", documento: str = ""):
    """Dados OFICIAIS e atualizados do produto no SafetyChem, separados por documento: ficha técnica, FDS
    (antiga FISPQ) e ficha de emergência. Prevalecem sobre a documentação antiga do catálogo e da pasta."""
    documentos._checar_chave(request)
    produto, documento = produto.strip()[:120], documento.strip()[:60]
    if not documentos.normalizar(produto):
        raise HTTPException(400, "Informe o produto (ex.: AG 2032).")
    achados = buscar(produto)
    if not achados:
        sugestoes = documentos.parecidos(_itens_busca(), produto)
        return {"encontrados": 0, "produtos": [], "produtos_parecidos": sugestoes,
                "orientacao": "Este produto não está no SafetyChem. Use a documentação do catálogo (boletim, "
                              "FISPQ/FDS), avisando que pode estar desatualizada."
                              + (" Ou pergunte se o usuário quis dizer um destes produtos." if sugestoes else "")}
    return {"encontrados": len(achados),
            "produtos": [_por_documento(i["_dados"], i["_importado_em"], documento, i["_ficha"], i["_fds"], i["_sds"])
                         for i in achados],
            "orientacao": "Documentação OFICIAL e atualizada (SafetyChem). Cada documento vem separado: ficha_tecnica "
                          "(aplicação e propriedades de uso), fds (segurança, 16 seções da ABNT NBR 14725, antiga "
                          "FISPQ), sds (a mesma ficha de segurança em inglês, para clientes do exterior) e "
                          "ficha_emergencia (transporte e emergência). Prevalece sobre a documentação antiga do catálogo e da pasta: "
                          "em caso de diferença, use esta e avise que o documento antigo está desatualizado. Cite o "
                          "documento e a versão (ex.: \"FDS SafetyChem, versão 04\"). Para o usuário abrir um "
                          "documento, use o link_markdown dele exatamente como veio."}


# ------------------------------------------------------------------ fichas técnicas (PDF publicado no SafetyChem)
# A ficha técnica não sai no XML: o robô (ou o admin) baixa o PDF publicado e envia aqui. Fica só a versão mais
# recente de cada produto; o PDF é guardado para o download e o texto é separado por seção para o agente.
FICHA_MAX_MB = 20
SECOES_FICHA = [  # (chave, título como aparece na ficha, sem acento e em minúsculas)
    ("aplicacao", r"aplicacao|aplicacoes"),
    ("especificacoes_tecnicas", r"especificacoes tecnicas|propriedades tipicas|caracteristicas tecnicas"),
    ("caracteristicas_e_vantagens", r"caracteristicas e vantagens|caracteristicas|vantagens"),
    ("tempos_de_reatividade", r"tempos de reatividade|reatividade"),
    ("processo_sugerido", r"processo sugerido|modo de uso|modo de aplicacao|instrucoes de uso"),
    ("seguranca", r"seguranca"),
    ("embalagens", r"embalagens|embalagem"),
    ("armazenamento_e_residuos", r"armazenamento e residuos|armazenamento|validade"),
    ("observacoes", r"observacoes|observacao"),
]
RX_LIXO_FICHA = re.compile(  # cabeçalho e rodapé repetidos em toda página
    r"^(vers[aã]o:?\s*\d*|data:?\s*[\d/]*|ficha t[eé]cnica|produto:.*|as informa[cç][oõ]es aqui apresentadas.*|"
    r"em condi[cç][oõ]es controladas.*|deste documento pode ser reproduzido.*|sujeita o infrator.*|rua:.*)$", re.I)
RX_ARQUIVO_FICHA = re.compile(r"ficha[ _]t[eé]cnica[ _-]+(.+?)[ _-]+v(\d+)[ _-]+([\d.]{8,20})", re.I)


RX_VERSAO_ARQUIVO = re.compile(r"[ _-]v(\d+)[ _-]+([\d.]{8,20})(?:\.pdf)?$", re.I)


def ordem_do_arquivo(nome_arquivo: str, data: str = "") -> str:
    """Chave para comparar versões: "0004|20250224111427" (versão, depois carimbo de publicação)."""
    m = RX_VERSAO_ARQUIVO.search(nome_arquivo or "")
    versao = m.group(1) if m else "0"
    carimbo = re.sub(r"\D", "", m.group(2)).ljust(14, "0")[:14] if m else ""
    return f"{int(versao):04d}|{carimbo or data.replace('-', '')}"


def _dir_fichas() -> str:
    d = os.path.join(settings.DATA_DIR, "safetychem", "fichas")
    os.makedirs(d, exist_ok=True)
    return d


def fichas_todas() -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM safetychem_fichas ORDER BY nome")]


def _id_ficha(chave: str) -> str:
    """Link estável por produto: continua valendo quando sai uma versão nova (sempre entrega a mais recente)."""
    return documentos._id("safetychem-ficha/" + chave)


def _link_ficha(t: dict) -> str:
    return f"/api/safetychem/fichas/{_id_ficha(t['chave'])}"


def _rotulo_ficha(t: dict) -> str:
    return f"Ficha técnica {t['nome']} (SafetyChem" + (f", versão {t['versao']}" if t.get("versao") else "") + ")"


def _ficha_publica(t: dict) -> dict:
    d = json.loads(t["dados"])
    out = {"versao": t["versao"] or "", "data": d.get("data", ""), "descricao": d.get("descricao", ""),
           "secoes": d.get("secoes", {}), "link_markdown": f"[📄 {_rotulo_ficha(t)}]({_link_ficha(t)})"}
    if not out["secoes"]:
        out["texto"] = d.get("texto", "")
    return out


def ler_ficha(texto: str, nome_arquivo: str) -> dict:
    """Separa a ficha técnica em seções pelos títulos (APLICAÇÃO, ESPECIFICAÇÕES TÉCNICAS...)."""
    m_arq = RX_ARQUIVO_FICHA.search(nome_arquivo or "")
    m_prod = re.search(r"Produto:[ \t]*([^\n]*)(?:\n[ \t]*([^\n]+))?", texto or "")
    nome = (m_prod.group(1).strip() or (m_prod.group(2) or "").strip()) if m_prod else ""
    nome = nome or (m_arq.group(1).replace("_", " ").strip() if m_arq else "")
    m_ver = re.search(r"Vers[aã]o:\s*(\d+)", texto or "")
    versao = m_ver.group(1) if m_ver else (m_arq.group(2) if m_arq else "")
    m_data = re.search(r"Data:\s*(\d{2})/(\d{2})/(\d{4})", texto or "")
    data = f"{m_data.group(3)}-{m_data.group(2)}-{m_data.group(1)}" if m_data else ""
    carimbo = re.sub(r"\D", "", m_arq.group(3)).ljust(14, "0")[:14] if m_arq else ""
    secoes, atual, descricao = {}, None, []
    for linha in (texto or "").splitlines():
        linha = linha.strip()
        if not linha or RX_LIXO_FICHA.match(linha) or documentos.normalizar(linha) == documentos.normalizar(nome):
            continue
        titulo = documentos.normalizar(linha)
        chave = next((k for k, rx in SECOES_FICHA if re.fullmatch(rx, titulo)), None) if linha.upper() == linha else None
        if chave:
            atual = chave
            secoes.setdefault(atual, [])
            continue
        (secoes[atual] if atual else descricao).append(linha)
    return {"nome": nome, "versao": versao, "data": data,
            # ordem para saber qual é a mais nova: versão, depois data/carimbo de publicação
            "ordem": f"{int(versao or 0):04d}|{carimbo or data.replace('-', '')}",
            "descricao": " ".join(descricao).strip(),
            "secoes": {k: "\n".join(v).strip() for k, v in secoes.items() if v}, "texto": (texto or "").strip()}


async def importar_ficha(dados: bytes, nome_arquivo: str, autor: str, nome_produto: str = "") -> dict:
    if not dados.startswith(b"%PDF"):
        raise HTTPException(415, "A ficha técnica precisa ser o PDF publicado no SafetyChem.")
    if not settings.CONVERSOR_URL:
        raise HTTPException(503, "O conversor de arquivos não está configurado: não dá para ler o PDF.")
    try:
        # com OCR: em PDF digital o conversor lê só o que é imagem (ex.: fórmulas) e mantém o texto que já existe
        _, info = await conhecimento._conversor(dados, nome_arquivo or "ficha.pdf", ocr=True)
    except conhecimento.ErroProcessamento as e:
        raise HTTPException(422, str(e))
    f = ler_ficha(info["texto"], nome_arquivo)
    if nome_produto:  # o nome comercial do SafetyChem (sincronização) vale mais que o lido do PDF
        f["nome"] = nome_produto
    if not f["nome"]:
        raise HTTPException(422, "Não achei o nome do produto na ficha (linha \"Produto:\").")
    chave = _chave(f["nome"])
    sha = hashlib.sha256(dados).hexdigest()
    with db.conn() as c:
        atual = c.execute("SELECT sha256, ordem, versao FROM safetychem_fichas WHERE chave=?", (chave,)).fetchone()
    base = {"produto": f["nome"], "versao": f["versao"], "arquivo": nome_arquivo}
    if atual and atual["sha256"] == sha:
        return {**base, "resultado": "sem_alteracao"}
    if atual and f["ordem"] < atual["ordem"]:
        return {**base, "resultado": "mais_antiga", "versao_atual": atual["versao"]}
    caminho = os.path.join(_dir_fichas(), f"{sha}.pdf")
    with open(caminho + ".tmp", "wb") as fh:
        fh.write(dados)
    os.replace(caminho + ".tmp", caminho)
    gravado = {k: f[k] for k in ("data", "descricao", "secoes", "texto")}
    with db.conn() as c:
        c.execute("INSERT INTO safetychem_fichas (chave, nome, versao, data_versao, ordem, arquivo_nome, sha256, dados, "
                  "importado_em, importado_por) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chave) DO UPDATE SET "
                  "nome=excluded.nome, versao=excluded.versao, data_versao=excluded.data_versao, ordem=excluded.ordem, "
                  "arquivo_nome=excluded.arquivo_nome, sha256=excluded.sha256, dados=excluded.dados, "
                  "importado_em=excluded.importado_em, importado_por=excluded.importado_por",
                  (chave, f["nome"], f["versao"], f["data"], f["ordem"], nome_arquivo or f"{f['nome']}.pdf", sha,
                   json.dumps(gravado, ensure_ascii=False), db.agora(), autor))
    if atual and atual["sha256"] != sha:
        try:
            os.remove(os.path.join(_dir_fichas(), f"{atual['sha256']}.pdf"))
        except OSError:
            pass
    return {**base, "resultado": "atualizada" if atual else "nova", "secoes": sorted(f["secoes"])}


@router.post("/api/admin/safetychem/fichas")
async def importar_ficha_rota(request: Request, admin: dict = Depends(admin_ou_robo)):
    """Uma ficha técnica (PDF) por envio; nome do arquivo no cabeçalho X-Arquivo-Nome."""
    limite = FICHA_MAX_MB * 1024 * 1024
    dados = bytearray()
    async for parte in request.stream():
        dados += parte
        if len(dados) > limite:
            raise HTTPException(413, f"O PDF passou de {FICHA_MAX_MB} MB.")
    nome = unquote(request.headers.get("x-arquivo-nome", ""))[:200]
    r = await importar_ficha(bytes(dados), nome, admin["username"])
    db.set_meta("safetychem_ultima_ficha", json.dumps({**r, "quando": db.agora(), "por": admin["username"]},
                                                      ensure_ascii=False))
    db.auditar(admin["username"], "importar_ficha_tecnica", r["produto"], f"{r['resultado']} (versão {r['versao']})")
    return r


@router.get("/api/safetychem/fichas/{doc_id}")
def baixar_ficha(doc_id: str, user: dict = Depends(usuario_atual)):
    """PDF da ficha técnica oficial (sempre a versão mais recente importada). Só para quem está logado."""
    t = next((f for f in fichas_todas() if _id_ficha(f["chave"]) == doc_id), None)
    caminho = os.path.join(_dir_fichas(), f"{t['sha256']}.pdf") if t else ""
    if not t or not os.path.isfile(caminho):
        raise HTTPException(404, "Ficha técnica não encontrada.")
    db.auditar(user["username"], "baixar_ficha_tecnica", t["nome"], f"versão {t['versao']}")
    nome = t["arquivo_nome"] if t["arquivo_nome"].lower().endswith(".pdf") else f"{t['arquivo_nome']}.pdf"
    return FileResponse(caminho, media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(nome)}", "Cache-Control": "private, no-store"})


# ------------------------------------------------------------------ busca nas fichas técnicas (por conteúdo)
# Para achar produto Flexível PARECIDO com outro (ex.: o boletim de um concorrente): o agente manda as palavras que
# descrevem o produto (aplicação, tipo, propriedades) e recebe as fichas técnicas oficiais que mais batem. A busca é
# por palavras (sem acento, singular/plural e masculino/feminino iguais); a comparação dos valores fica com o agente.
BUSCA_MAX_TEXTO = 300
BUSCA_LIMITE_PADRAO = 5
BUSCA_LIMITE_MAXIMO = 8
BUSCA_MAX_CARACTERES = 900  # por seção devolvida: o suficiente para comparar, sem estourar a conversa do agente
PESO_CAMPO = {  # onde a palavra aparece pesa diferente: aplicação e descrição dizem o que o produto É
    "nome": 3.0, "aplicacao": 3.0, "descricao": 2.0, "caracteristicas_e_vantagens": 2.0,
    "especificacoes_tecnicas": 1.5, "processo_sugerido": 1.0, "tempos_de_reatividade": 1.0, "texto": 1.0,
    "observacoes": 0.5, "seguranca": 0.3, "embalagens": 0.3, "armazenamento_e_residuos": 0.3,
}
SECOES_DEVOLVIDAS = ("aplicacao", "caracteristicas_e_vantagens", "especificacoes_tecnicas", "tempos_de_reatividade",
                     "processo_sugerido")
PALAVRAS_VAZIAS = set(
    "a o e as os um uma uns umas de da do das dos em no na nos nas para pra por pelo pela pelos pelas com sem "
    "que ou se ao aos sua seu suas seus como mais menos muito muita tipo produto produtos similar similares "
    "parecido parecida parecidos parecidas equivalente equivalentes concorrente flexx grupo "
    "ficha fichas tecnica tecnicas boletim boletins uso usado usada indicado indicada qual quais algum alguma "
    "the of and for with".split())
_indice_fichas: dict = {"assinatura": None, "itens": []}


def _raiz(palavra: str) -> str:
    """Raiz simples em português, igual dos dois lados da busca: adesivos = adesivo = adesiva, colchões = colchão."""
    p = palavra
    if p.isdigit() or len(p) <= 3:
        return p
    for sufixo, troca in (("oes", "ao"), ("aes", "ao"), ("ais", "al"), ("eis", "el"), ("ois", "ol"),
                          ("res", "r"), ("zes", "z"), ("ns", "m")):
        if p.endswith(sufixo) and len(p) > len(sufixo) + 2:
            p = p[: -len(sufixo)] + troca
            break
    else:
        if p.endswith("s") and not p.endswith("ss"):
            p = p[:-1]
    if len(p) > 4 and p[-1] in "aoe":
        p = p[:-1]
    return p


def _raizes(texto: str) -> list[str]:
    return [_raiz(p) for p in documentos.normalizar(texto).split()]


def _indice_busca() -> list[dict]:
    """Raízes de cada campo de cada ficha; refeito só quando alguma ficha muda."""
    with db.conn() as c:
        assinatura = c.execute("SELECT COUNT(*) AS n, group_concat(chave || ':' || sha256, '|') AS s "
                               "FROM safetychem_fichas").fetchone()
    assinatura = (assinatura["n"], hashlib.sha256((assinatura["s"] or "").encode()).hexdigest())
    if _indice_fichas["assinatura"] == assinatura:
        return _indice_fichas["itens"]
    itens = []
    for t in fichas_todas():
        d = json.loads(t["dados"])
        campos = {"nome": t["nome"], "descricao": d.get("descricao", ""), **(d.get("secoes") or {})}
        if not d.get("secoes"):
            campos["texto"] = d.get("texto", "")
        raizes = {k: _raizes(v) for k, v in campos.items() if v and k in PESO_CAMPO}
        itens.append({"ficha": t, "dados": d,
                      "conjuntos": {k: set(v) for k, v in raizes.items()},
                      "sequencias": {k: " " + " ".join(v) + " " for k, v in raizes.items()}})
    _indice_fichas.update(assinatura=assinatura, itens=itens)
    return itens


def _termos_da_busca(texto: str) -> list[tuple[str, str]]:
    """(palavra como veio, raiz), sem palavras vazias e sem repetir raiz."""
    vistos, out = set(), []
    for palavra in documentos.normalizar(texto).split():
        if palavra in PALAVRAS_VAZIAS or (len(palavra) < 2 and not palavra.isdigit()):
            continue
        r = _raiz(palavra)
        if r not in vistos:
            vistos.add(r)
            out.append((palavra, r))
    return out


def _cortar(texto: str, limite: int = BUSCA_MAX_CARACTERES) -> str:
    texto = (texto or "").strip()
    return texto if len(texto) <= limite else texto[:limite].rsplit(" ", 1)[0] + " …"


def buscar_fichas(texto: str, limite: int = BUSCA_LIMITE_PADRAO) -> tuple[list[dict], list[str]]:
    """Fichas técnicas que mais batem com a descrição, da mais para a menos parecida."""
    termos = _termos_da_busca(texto)
    itens = _indice_busca()
    if not termos or not itens:
        return [], [p for p, _ in termos]
    total = len(itens)
    # palavra que aparece em quase toda ficha ("poliuretano") vale pouco; a rara ("calçado") vale muito
    em_quantas = {r: sum(1 for i in itens if any(r in s for s in i["conjuntos"].values())) for _, r in termos}
    achados = []
    for i in itens:
        pontos, encontrados = 0.0, []
        for palavra, r in termos:
            peso = 0.0
            for campo, conjunto in i["conjuntos"].items():
                if r in conjunto:
                    peso = max(peso, PESO_CAMPO[campo])
                elif len(r) >= 5 and any(p.startswith(r) for p in conjunto):  # "calcad" acha "calcadista"
                    peso = max(peso, PESO_CAMPO[campo] * 0.6)
            if peso:
                raridade = 1 + math.log(total / max(em_quantas[r], 1))
                pontos += peso * raridade
                encontrados.append(palavra)
        if not encontrados:
            continue
        for (_, a), (_, b) in zip(termos, termos[1:]):  # palavras juntas, como na busca ("alta resiliencia")
            for campo, seq in i["sequencias"].items():
                if f" {a} {b} " in seq:
                    pontos += PESO_CAMPO[campo]
                    break
        achados.append((pontos, len(encontrados), i, encontrados))
    achados.sort(key=lambda x: (-x[0], -x[1], x[2]["ficha"]["nome"]))
    out = []
    for pontos, _, i, encontrados in achados[:limite]:
        t, d = i["ficha"], i["dados"]
        secoes = d.get("secoes") or {}
        p = {"nome": t["nome"], "versao": t["versao"] or "", "data": d.get("data", ""),
             "palavras_encontradas": encontrados, "descricao": _cortar(d.get("descricao", ""), 400)}
        for k in SECOES_DEVOLVIDAS:
            if secoes.get(k):
                p[k] = _cortar(secoes[k])
        if not secoes:
            p["texto"] = _cortar(d.get("texto", ""), 1500)
        p["link_markdown"] = f"[📄 {_rotulo_ficha(t)}]({_link_ficha(t)})"
        out.append(p)
    return out, [p for p, _ in termos]


@router.get("/api/ferramentas/safetychem/buscar")
async def ferramenta_buscar_fichas(request: Request, texto: str = "", limite: int = BUSCA_LIMITE_PADRAO):
    """Procura nas fichas técnicas oficiais (SafetyChem) os produtos que batem com uma descrição: aplicação, tipo de
    produto e propriedades. Para achar produto Flexível parecido com outro (ex.: de um concorrente)."""
    documentos._checar_chave(request)
    texto = texto.strip()[:BUSCA_MAX_TEXTO]
    limite = max(1, min(limite, BUSCA_LIMITE_MAXIMO))
    if not _termos_da_busca(texto):
        raise HTTPException(400, "Descreva o produto procurado: aplicação, tipo e propriedades "
                                 "(ex.: adesivo calçado base solvente).")
    produtos, palavras = buscar_fichas(texto, limite)
    if not produtos:
        return {"encontrados": 0, "produtos": [], "palavras_usadas": palavras,
                "orientacao": "Nenhuma ficha técnica oficial bate com essa descrição. Tente outras palavras (aplicação, "
                              "tipo de produto, mercado) ou procure no catálogo; se nada aparecer, diga que não "
                              "achou produto parecido e sugira falar com a área técnica."}
    return {"encontrados": len(produtos), "produtos": produtos, "palavras_usadas": palavras,
            "orientacao": "Fichas técnicas OFICIAIS (SafetyChem) que mais batem com a descrição, da mais para a menos "
                          "parecida. Bater palavras não prova que o produto é equivalente: compare aplicação e "
                          "especificações com o produto procurado e diga o que é igual e o que é diferente. Use só o "
                          "que está na ficha; cite o produto e a versão (ex.: \"ficha técnica FLEXX AG 2047, versão "
                          "03\"). Para o usuário abrir a ficha, use o link_markdown exatamente como veio."}

# ------------------------------------------------------------------ sincronização automática com o SafetyChem
# Entra com o usuário do robô, lista as fichas técnicas publicadas (a mesma pesquisa da tela Documentos > Documentos
# publicados) e baixa SÓ as novas ou com versão nova, pelo link público do documento. Roda todo dia no horário
# SAFETYCHEM_HORARIO e pelo botão "Sincronizar agora". Nada passa por máquina de usuário: o PDF vai direto do
# SafetyChem para a plataforma.
DOC_FICHA_TECNICA = 49           # tipo "Ficha Técnica" no SafetyChem
# documento da plataforma -> tipo de documento no SafetyChem (tela Documentos publicados > Documento)
TIPOS_SAFETYCHEM = {"ficha_tecnica": DOC_FICHA_TECNICA, "fds": 1, "sds": 2}
NOMES_TIPO = {"ficha_tecnica": "Fichas técnicas", "fds": "FDS", "sds": "SDS"}
POR_PAGINA = int(os.getenv("SAFETYCHEM_POR_PAGINA", "500"))  # como na tela: 500 por página, uma de cada vez
PAUSA_ENTRE_DOWNLOADS = float(os.getenv("SAFETYCHEM_PAUSA", "0.5"))
_sinc: dict = {"tarefa": None}


class ErroSafetychem(Exception):
    pass


def sincronizacao_configurada() -> bool:
    return bool(settings.SAFETYCHEM_USUARIO and settings.SAFETYCHEM_SENHA)


def _situacao_sinc() -> dict:
    v = db.get_meta("safetychem_sincronizacao")
    return json.loads(v) if v else {}


def _gravar_sinc(d: dict) -> None:
    db.set_meta("safetychem_sincronizacao", json.dumps(d, ensure_ascii=False))


async def _entrar(c: httpx.AsyncClient) -> None:
    r = await c.post("/Login/Login?Area=Default", json={
        "Password": hashlib.md5(settings.SAFETYCHEM_SENHA.encode()).hexdigest(), "PasswordReset": "", "URAAD": False,
        "Username": settings.SAFETYCHEM_USUARIO, "EhResetSenha": False})
    if r.status_code != 200 or not (r.json() if "json" in r.headers.get("content-type", "") else {}).get("LoginValido"):
        raise ErroSafetychem("O SafetyChem recusou o usuário/senha do robô (SAFETYCHEM_USUARIO/SAFETYCHEM_SENHA).")


async def _listar(c: httpx.AsyncClient, tipo_documento: int) -> list[dict]:
    modelo = (await c.get("/NewSafetyChem/DocumentoPublicado/GetModel")).json()["model"]
    for k in ("EmpresaDocumentos", "EmpresaUnidades", "EmpresasCombo"):
        modelo[k] = None
    dp = modelo["DadosPesquisa"]
    dp.update({"TipoDocumento": tipo_documento, "IdiomaPesquisaId": 0, "VinculoId": modelo.get("EmpresaLogadaId"),
               "Areas": [], "CodigosEmpresa": [], "FiltrosPesquisa": [], "FiltroPesquisaDinamico": False,
               "AplicaPaginacao": True, "PreencherColunasGrid": True,
               "UsuarioColunasGrid": modelo.get("UsuarioColunasGrid") or []})
    todas = []
    for pagina in range(1, 41):  # uma página de 500 por vez
        dp["Paginacao"] = {"page": pagina, "rowsPerPage": POR_PAGINA, "totalPages": 0, "sortField": "", "asc": True,
                           "totalRegistros": 0, "rowsPerPageOptions": [10, 20, 50, 100, 200, 500]}
        r = await c.post("/NewSafetyChem/DocumentoPublicado/GetDocumentosPublicados", json=modelo)
        docs = (r.json().get("model") or {}).get("DocumentosPublicados") or []
        todas += docs
        if len(docs) < POR_PAGINA:
            break
    out = []
    for d in todas:
        nome = ((d.get("Produto") or {}).get("ProdutoInformacaoIdioma") or {}).get("NomeComercial") or ""
        arquivo = d.get("NomeArquivo") or ""
        demonstracao = "demonstra" in arquivo.lower() or re.search(r"\bdemo\b", nome, re.I)
        if d.get("DocumentoPublicadoGUID") and nome and not demonstracao and d.get("UltimaVersao", True) \
                and d.get("Ativo", True):
            out.append({"nome": nome.strip(), "arquivo": d.get("NomeArquivo") or f"{nome}.pdf",
                        "guid": d["DocumentoPublicadoGUID"], "data": (d.get("DataPublicacao") or "")[:10]})
    return out


async def sincronizar_fichas(autor: str = "robo-safetychem") -> dict:
    """Ficha técnica, FDS e SDS: lista cada tipo e baixa só o que é novo ou tem versão nova."""
    inicio = time.time()
    zerado = {"listadas": 0, "a_baixar": 0, "processadas": 0, "novas": 0, "atualizadas": 0, "sem_alteracao": 0,
              "mais_antigas": 0, "erros": 0}
    sit = {"situacao": "rodando", "inicio": db.agora(), "por": autor, **{k: 0 for k in zerado}, "erros": [],
           "por_tipo": {t: dict(zerado) for t in TIPOS_SAFETYCHEM}}
    _gravar_sinc(sit)

    def soma(tipo: str, campo: str, n: int = 1) -> None:
        sit[campo] += n
        sit["por_tipo"][tipo][campo] += n

    try:
        async with httpx.AsyncClient(base_url=settings.SAFETYCHEM_URL, timeout=httpx.Timeout(120, connect=20),
                                     follow_redirects=True, headers={"User-Agent": "agente-grupo-flexivel/1.0",
                                                                     "Accept": "application/json, text/plain, */*"}) as c:
            await _entrar(c)
            pendentes = []
            for tipo, tipo_sc in TIPOS_SAFETYCHEM.items():
                lista = await _listar(c, tipo_sc)
                soma(tipo, "listadas", len(lista))
                atuais = {r["chave"]: r for r in (fichas_todas() if tipo == "ficha_tecnica" else docs_todos(tipo))}
                arquivos = {r["arquivo_nome"] for r in atuais.values()}
                for d in lista:
                    atual = atuais.get(_chave(d["nome"]))
                    if d["arquivo"] in arquivos or (atual and atual["ordem"] >= ordem_do_arquivo(d["arquivo"], d["data"])):
                        soma(tipo, "sem_alteracao")
                    else:
                        pendentes.append((tipo, d))
                        soma(tipo, "a_baixar")
            _gravar_sinc(sit)
            for tipo, d in pendentes:
                try:
                    if not re.fullmatch(r"[0-9a-fA-F-]{32,40}", d["guid"]):
                        raise ErroSafetychem("identificador do documento inválido")
                    r = await c.get(f"/Doc?{d['guid']}")  # link público do documento publicado
                    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                        raise ErroSafetychem(f"download devolveu {r.status_code}")
                    if tipo == "ficha_tecnica":
                        res = await importar_ficha(r.content, d["arquivo"], autor, nome_produto=d["nome"])
                    else:
                        res = await importar_seguranca(tipo, r.content, d["arquivo"], autor, d["nome"], d["guid"])
                    soma(tipo, {"nova": "novas", "atualizada": "atualizadas", "sem_alteracao": "sem_alteracao",
                                "mais_antiga": "mais_antigas"}[res["resultado"]])
                except (ErroSafetychem, HTTPException, httpx.HTTPError) as e:
                    detalhe = getattr(e, "detail", None) or str(e) or type(e).__name__
                    sit["erros"].append(f"{NOMES_TIPO[tipo]} {d['nome']}: {detalhe}"[:200])
                    sit["por_tipo"][tipo]["erros"] += 1
                soma(tipo, "processadas")
                if sit["processadas"] % 5 == 0:
                    _gravar_sinc(sit)
                await asyncio.sleep(PAUSA_ENTRE_DOWNLOADS)
        sit["situacao"] = "ok" if not sit["erros"] else "ok_com_erros"
    except (ErroSafetychem, httpx.HTTPError, ValueError, KeyError) as e:
        sit["situacao"] = "erro"
        sit["mensagem"] = str(e) if isinstance(e, ErroSafetychem) else f"Falha ao falar com o SafetyChem ({type(e).__name__})."
        log.warning("sincronização SafetyChem falhou: %s", e)
    sit["total_erros"] = len(sit["erros"])
    sit["erros"] = sit["erros"][:30]
    sit["fim"] = db.agora()
    sit["segundos"] = round(time.time() - inicio)
    _gravar_sinc(sit)
    db.auditar(autor, "sincronizar_safetychem", "", f"{sit['situacao']}: {sit['listadas']} listadas, {sit['novas']} novas, "
               f"{sit['atualizadas']} atualizadas, {sit['total_erros']} erros")
    log.info("sincronização SafetyChem: %s", {k: v for k, v in sit.items() if k != "erros"})
    return sit


def iniciar_sincronizacao(autor: str) -> bool:
    """Dispara em segundo plano (uma por vez). Devolve False se já há uma rodando."""
    t = _sinc["tarefa"]
    if t and not t.done():
        return False
    _sinc["tarefa"] = asyncio.create_task(sincronizar_fichas(autor))
    return True


def _proxima_execucao(agora: datetime) -> datetime | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", settings.SAFETYCHEM_HORARIO.strip())
    if not m:
        return None
    alvo = agora.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    return alvo if alvo > agora else alvo + timedelta(days=1)


async def agendador() -> None:
    """Roda a sincronização todo dia no horário configurado (enquanto a plataforma estiver no ar)."""
    if not sincronizacao_configurada():
        return
    sit = _situacao_sinc()
    if sit.get("situacao") == "rodando":  # o container reiniciou no meio: marca e segue
        _gravar_sinc({**sit, "situacao": "erro", "mensagem": "Interrompida por reinício da plataforma."})
    while True:
        agora = datetime.now(tempo.FUSO)
        proxima = _proxima_execucao(agora)
        if not proxima:
            return
        await asyncio.sleep(max(1.0, (proxima - agora).total_seconds()))
        iniciar_sincronizacao("robo-safetychem")
        await asyncio.sleep(61)


@router.post("/api/admin/safetychem/sincronizar")
async def sincronizar_agora(admin: dict = Depends(exigir_admin)):
    if not sincronizacao_configurada():
        raise HTTPException(400, "Sincronização não configurada: defina SAFETYCHEM_USUARIO e SAFETYCHEM_SENHA no .env.")
    if not iniciar_sincronizacao(admin["username"]):
        raise HTTPException(409, "Já há uma sincronização em andamento.")
    return {"iniciada": True}


@router.get("/api/admin/safetychem/sincronizacao")
def situacao_sincronizacao(admin: dict = Depends(exigir_admin)):
    proxima = _proxima_execucao(datetime.now(tempo.FUSO)) if sincronizacao_configurada() else None
    return {"configurada": sincronizacao_configurada(), "horario": settings.SAFETYCHEM_HORARIO,
            "proxima": proxima.isoformat() if proxima else None, **_situacao_sinc()}


# ------------------------------------------------------------------ FDS (PT) e SDS (EN) publicadas: texto por seção
# Fica só o texto (o PDF de ~1 MB cada fica no link público oficial do SafetyChem, para não lotar o disco).
# As 16 seções da ABNT NBR 14725 / GHS: "1 - IDENTIFICAÇÃO" (FDS) ou "SECTION 1: IDENTIFICATION" (SDS).
SECOES_SEGURANCA = {
    1: "identificacao", 2: "identificacao_de_perigos", 3: "composicao", 4: "primeiros_socorros",
    5: "combate_a_incendio", 6: "derramamento_ou_vazamento", 7: "manuseio_e_armazenamento",
    8: "controle_de_exposicao_e_epi", 9: "propriedades_fisicas_e_quimicas", 10: "estabilidade_e_reatividade",
    11: "informacoes_toxicologicas", 12: "informacoes_ecologicas", 13: "destinacao_final", 14: "transporte",
    15: "regulamentacoes", 16: "outras_informacoes",
}
RX_SECAO_SEGURANCA = re.compile(r"^(?:section\s+)?(\d{1,2})\s*[-.:–]\s*(\S.{2,120})$", re.I)
RX_LIXO_SEGURANCA = re.compile(  # cabeçalho repetido em toda página da FDS/SDS
    r"^(fds|fispq|sds|em conformidade com.*|in accordance with.*|classification and labell?ing.*|chapter 1\.5.*|"
    r"ficha com dados de seguran[cç]a|ficha de informa[cç][oõ]es de seguran[cç]a.*|safety data sheet|produto:.*|"
    r"product:.*|(vers[aã]o|revision):.*(p[aá]gina|pages?):.*)$", re.I)
ROTULO_SEGURANCA = {"fds": "FDS", "sds": "SDS (inglês)"}


def docs_todos(tipo: str) -> list[dict]:
    tabela = {"fds": "safetychem_fds", "sds": "safetychem_sds"}[tipo]
    with db.conn() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM {tabela} ORDER BY nome")]


def _link_pdf_seguranca(r: dict) -> str:
    return f"{settings.SAFETYCHEM_URL}/Doc?{r['guid']}"


def _rotulo_seguranca(tipo: str, r: dict) -> str:
    return f"{ROTULO_SEGURANCA[tipo]} {r['nome']} (SafetyChem" + (f", versão {r['versao']}" if r.get("versao") else "") + ")"


def _seguranca_publica(tipo: str, r: dict) -> dict:
    d = json.loads(r["dados"])
    return {"versao": r["versao"] or "", "data": d.get("data", ""), "secoes": d.get("secoes", {}),
            "link_markdown": f"[📄 {_rotulo_seguranca(tipo, r)}]({_link_pdf_seguranca(r)})"}


def ler_seguranca(texto: str) -> dict:
    """Separa a FDS/SDS nas 16 seções, sem o cabeçalho repetido nas páginas."""
    m_prod = re.search(r"(?:Produto|Product):[ \t]*([^\n]+)", texto or "")
    m_ver = re.search(r"(?:Vers[aã]o|Revision):\s*(\d+)", texto or "")
    data = ""
    if m := re.search(r"Data:\s*(\d{2})/(\d{2})/(\d{4})", texto or ""):          # FDS: dd/mm/aaaa
        data = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    elif m := re.search(r"Date:\s*(\d{2})/(\d{2})/(\d{4})", texto or ""):        # SDS: mm/dd/aaaa
        data = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    secoes, atual, proxima = {}, None, 1
    for linha in (texto or "").splitlines():
        linha = linha.strip()
        if not linha or RX_LIXO_SEGURANCA.match(linha):
            continue
        m = RX_SECAO_SEGURANCA.match(linha)
        # título de seção: número na sequência (evita notas como "2 Classificação de perigos: ...") e em maiúsculas
        if m and int(m.group(1)) == proxima and m.group(2).upper() == m.group(2) and int(m.group(1)) in SECOES_SEGURANCA:
            atual = SECOES_SEGURANCA[int(m.group(1))]
            secoes[atual] = []
            proxima += 1
            continue
        if atual:
            secoes[atual].append(linha)
    return {"nome": m_prod.group(1).strip() if m_prod else "", "versao": m_ver.group(1) if m_ver else "",
            "data": data, "secoes": {k: "\n".join(v).strip() for k, v in secoes.items() if v}}


async def importar_seguranca(tipo: str, dados: bytes, nome_arquivo: str, autor: str, nome_produto: str = "",
                             guid: str = "") -> dict:
    if not dados.startswith(b"%PDF"):
        raise HTTPException(415, "A FDS/SDS precisa ser o PDF publicado no SafetyChem.")
    if not settings.CONVERSOR_URL:
        raise HTTPException(503, "O conversor de arquivos não está configurado: não dá para ler o PDF.")
    try:  # FDS/SDS são PDF digital: sem OCR (mais rápido)
        _, info = await conhecimento._conversor(dados, nome_arquivo or "fds.pdf", ocr=False)
    except conhecimento.ErroProcessamento as e:
        raise HTTPException(422, str(e))
    f = ler_seguranca(info["texto"])
    nome = nome_produto or f["nome"]
    if not nome or len(f["secoes"]) < 8:
        raise HTTPException(422, "Não reconheci as seções da FDS/SDS neste PDF.")
    chave, sha = _chave(nome), hashlib.sha256(dados).hexdigest()
    ordem = ordem_do_arquivo(nome_arquivo, f["data"])
    tabela = {"fds": "safetychem_fds", "sds": "safetychem_sds"}[tipo]
    with db.conn() as c:
        atual = c.execute(f"SELECT sha256, ordem, versao FROM {tabela} WHERE chave=?", (chave,)).fetchone()
    base = {"produto": nome, "versao": f["versao"], "arquivo": nome_arquivo}
    if atual and atual["sha256"] == sha:
        return {**base, "resultado": "sem_alteracao"}
    if atual and ordem < atual["ordem"]:
        return {**base, "resultado": "mais_antiga", "versao_atual": atual["versao"]}
    with db.conn() as c:
        c.execute(f"INSERT INTO {tabela} (chave, nome, versao, data_versao, ordem, arquivo_nome, sha256, guid, dados, "
                  "importado_em, importado_por) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chave) DO UPDATE SET "
                  "nome=excluded.nome, versao=excluded.versao, data_versao=excluded.data_versao, ordem=excluded.ordem, "
                  "arquivo_nome=excluded.arquivo_nome, sha256=excluded.sha256, guid=excluded.guid, dados=excluded.dados, "
                  "importado_em=excluded.importado_em, importado_por=excluded.importado_por",
                  (chave, nome, f["versao"], f["data"], ordem, nome_arquivo, sha, guid,
                   json.dumps({"data": f["data"], "secoes": f["secoes"]}, ensure_ascii=False), db.agora(), autor))
    return {**base, "resultado": "atualizada" if atual else "nova", "secoes": len(f["secoes"])}
