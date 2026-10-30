/* Agentes do Grupo Flexível — frontend (SPA + PWA) */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const ICONE = {
  copiar: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3h11a2 2 0 012 2v11h-2V5H8zM5 7h10a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V9a2 2 0 012-2zm0 2v10h10V9z"/></svg>',
  ok: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16.2l-3.5-3.5L4 14.2l5 5 11-11-1.5-1.5z"/></svg>',
  like: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 21h4V9H2zm20-11a2 2 0 00-2-2h-6.3l1-4.6v-.3a1.5 1.5 0 00-.4-1L13.2 1 6.6 7.6A2 2 0 006 9v10a2 2 0 002 2h9a2 2 0 001.8-1.2l3-7.1c.1-.2.2-.5.2-.7z"/></svg>',
  dislike: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 3h-4v12h4zM2 14a2 2 0 002 2h6.3l-1 4.6v.3a1.5 1.5 0 00.4 1l1.1 1.1 6.6-6.6A2 2 0 0018 15V5a2 2 0 00-2-2H7a2 2 0 00-1.8 1.2l-3 7.1c-.1.2-.2.5-.2.7z"/></svg>',
  editar: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 17.3V21h3.7L17.8 9.9l-3.7-3.7zM20.7 7a1 1 0 000-1.4l-2.3-2.3a1 1 0 00-1.4 0l-1.8 1.8 3.7 3.7z"/></svg>',
  ouvir: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9v6h4l5 5V4L7 9zm13.5 3A4.5 4.5 0 0014 8v8a4.5 4.5 0 002.5-4zM14 3.2v2.1a7 7 0 010 13.4v2.1a9 9 0 000-17.6z"/></svg>',
  parar: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  lixo: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 1h4v2H4V4h4zM5 7h14l-1 14H6zm4 2v10h2V9zm4 0v10h2V9z"/></svg>',
};

const ABERTURA_PADRAO = 'Pergunte o que precisar. As respostas usam as bases da empresa liberadas para este agente.';
const MODO_AGENTE = { 'agent-chat': 'Agente', 'advanced-chat': 'Chatflow', chat: 'Chatbot' };

const RESULTADO_ACESSO = {
  ok: ['Entrou', 'ok'],
  senha_invalida: ['Senha inválida', 'falha'],
  nao_cadastrado: ['Sem acesso liberado', 'falha'],
  inativo: ['Acesso desativado', 'falha'],
  bloqueado: ['Bloqueado (tentativas)', 'falha'],
  desabilitado: ['Conta desabilitada no AD', 'falha'],
  nao_encontrado: ['Não existe no AD', 'falha'],
  erro_ldap: ['Erro ao falar com o AD', 'falha'],
};
const EVENTO_USO = {
  pergunta: ['Pergunta ao agente', 'ok'],
  erro: ['Erro na resposta', 'falha'],
  audio: ['Áudio transcrito', 'ok'],
  ouvir: ['Resposta ouvida', 'ok'],
};

const estado = {
  usuario: null,
  agentes: [],          // agentes que a pessoa pode usar
  agenteAtual: null,
  info: null,
  conversaAtual: null,
  sessaoChat: 0,        // muda a cada troca de conversa/agente: respostas atrasadas da anterior são ignoradas
  geracaoAgente: null,  // agente da resposta em andamento (para o botão parar)
  conversas: [],
  maisConversas: false,
  gerando: false,
  taskId: null,
  abort: null,
  usuarios: [],
  adminAgentes: [],     // todos os agentes (tela de administração)
};

const rota = (caminho = '', aid = estado.agenteAtual?.id) => `/api/agentes/${aid}${caminho}`;

/* ------------------------------------------------------------------ utilidades */
class ErroApi extends Error {
  constructor(status, mensagem) { super(mensagem); this.status = status; }
}

async function api(caminho, { metodo = 'GET', corpo, bruto, sinal, cabecalhos } = {}) {
  const opts = { method: metodo, credentials: 'same-origin', headers: { 'X-Requested-With': 'agente-pu', ...(cabecalhos || {}) }, signal: sinal };
  if (bruto !== undefined) {  // Blob enviado como está (ex.: áudio WAV)
    opts.headers['Content-Type'] = bruto.type;
    opts.body = bruto;
  } else if (corpo !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(corpo);
  }
  let r;
  try {
    r = await fetch(caminho, opts);
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ErroApi(0, 'Sem conexão com o servidor. Verifique a rede.');
  }
  if (!r.ok) {
    let msg = `Erro ${r.status}`;
    try {
      const j = await r.json();
      if (typeof j.detail === 'string') msg = j.detail;
      else if (Array.isArray(j.detail)) msg = 'Dados inválidos.';
    } catch { /* corpo não é JSON */ }
    if (r.status === 401 && !caminho.startsWith('/api/auth/') && estado.usuario) {
      estado.usuario = null;
      mostrarLogin(msg);
    }
    throw new ErroApi(r.status, msg);
  }
  return r;
}
const getJSON = async (c) => (await api(c)).json();
const enviarJSON = async (c, metodo, corpo) => (await api(c, { metodo, corpo })).json();

let toastTimer;
function toast(msg, ms = 3200) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

function iniciais(nome, login) {
  const partes = String(nome || login || '?').trim().split(/\s+/).filter(Boolean);
  if (partes.length === 1) return partes[0].slice(0, 2).toUpperCase();
  return (partes[0][0] + partes[partes.length - 1][0]).toUpperCase();
}

/* ícone do agente: emoji do Dify sobre a cor dele, ou a inicial do nome */
function iconeAgente(a, classe = 'agente-icone', id = '') {
  const emoji = a?.icone || '';
  const cor = /^#[0-9a-f]{3,8}$/i.test(a?.cor || '') ? a.cor : '';
  const txt = emoji || iniciais(a?.nome, '?').slice(0, 1);
  return `<span${id ? ` id="${id}"` : ''} class="${classe}${emoji ? '' : ' letra'}"${cor ? ` style="background:${cor}"` : ''} aria-hidden="true">${esc(txt)}</span>`;
}

function primeiroNome(nome, login) {
  const n = String(nome || '').trim().split(/\s+/)[0];
  if (n) return n.charAt(0).toUpperCase() + n.slice(1).toLowerCase();
  return login || '';
}

function dataHora(valor) {
  if (!valor) return '—';
  const d = typeof valor === 'number' ? new Date(valor * 1000) : new Date(valor);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function ehMobile() {
  return window.matchMedia('(max-width: 860px)').matches || window.matchMedia('(pointer: coarse)').matches;
}

async function copiarTexto(texto) {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = texto;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

/* ------------------------------------------------------------------ markdown */
if (window.DOMPurify) {
  DOMPurify.addHook('afterSanitizeAttributes', (no) => {
    if (no.tagName === 'A') {
      no.setAttribute('target', '_blank');
      no.setAttribute('rel', 'noopener noreferrer');
    }
  });
}

function renderMarkdown(el, texto, comCursor = false) {
  const html = window.marked ? marked.parse(texto || '', { gfm: true, breaks: true }) : esc(texto);
  el.innerHTML = window.DOMPurify ? DOMPurify.sanitize(html) : esc(texto);
  for (const t of el.querySelectorAll('table')) {
    if (!t.parentElement.classList.contains('tabela-wrap')) {
      const w = document.createElement('div');
      w.className = 'tabela-wrap';
      t.replaceWith(w);
      w.appendChild(t);
    }
  }
  if (comCursor) {
    let alvo = el;
    while (alvo.lastElementChild && !['BR', 'HR', 'IMG', 'TABLE'].includes(alvo.lastElementChild.tagName)) {
      alvo = alvo.lastElementChild;
    }
    if (alvo.tagName === 'DIV' && alvo.classList.contains('tabela-wrap')) alvo = el;
    const c = document.createElement('span');
    c.className = 'cursor';
    alvo.appendChild(c);
  }
}

/* ------------------------------------------------------------------ tema */
const TEMAS = ['auto', 'claro', 'escuro'];
const NOME_TEMA = { auto: 'automático', claro: 'claro', escuro: 'escuro' };

function lerTema() {
  try { return localStorage.getItem('agente-tema') || 'auto'; } catch { return 'auto'; }
}

function aplicarTema(tema) {
  if (tema === 'auto') delete document.documentElement.dataset.tema;
  else document.documentElement.dataset.tema = tema;
  $('#menu-tema').textContent = `Tema: ${NOME_TEMA[tema]}`;
}

function alternarTema() {
  const prox = TEMAS[(TEMAS.indexOf(lerTema()) + 1) % TEMAS.length];
  try { localStorage.setItem('agente-tema', prox); } catch { /* sem armazenamento */ }
  aplicarTema(prox);
}

/* ------------------------------------------------------------------ PWA */
let promptInstalar = null;

function ehStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
}

function ehIOS() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

function atualizarCardInstalar() {
  const card = $('#card-instalar');
  const btn = $('#btn-instalar');
  const texto = $('#instalar-texto');
  const ios = $('#instrucao-ios');
  if (ehStandalone()) {
    card.hidden = true;
    ios.hidden = true;
    return;
  }
  card.hidden = false;
  if (promptInstalar) {
    btn.hidden = false;
    btn.textContent = 'Instalar';
    texto.textContent = 'Acesse o Agente Técnico PU direto da tela inicial, como um aplicativo.';
  } else if (ehIOS()) {
    btn.hidden = false;
    btn.textContent = 'Como instalar';
    texto.textContent = 'No iPhone ou iPad, adicione o app à tela de início pelo Safari.';
  } else {
    btn.hidden = false;
    btn.textContent = 'Como instalar';
    texto.textContent = 'Use o app pela tela inicial do celular ou como janela própria no computador.';
  }
}

async function clicarInstalar() {
  if (promptInstalar) {
    promptInstalar.prompt();
    const escolha = await promptInstalar.userChoice.catch(() => null);
    promptInstalar = null;
    if (escolha && escolha.outcome === 'accepted') toast('Instalando o app…');
    atualizarCardInstalar();
    return;
  }
  const ios = $('#instrucao-ios');
  if (ehIOS()) {
    ios.hidden = !ios.hidden;
  } else {
    toast('No menu do navegador (⋮), toque em "Instalar app" ou "Adicionar à tela inicial".', 6000);
  }
}

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  promptInstalar = e;
  atualizarCardInstalar();
});
window.addEventListener('appinstalled', () => {
  promptInstalar = null;
  atualizarCardInstalar();
  toast('App instalado! Procure o ícone "Agente PU" na tela inicial.');
});

if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* sem PWA, o site funciona igual */ });
  });
}

/* ------------------------------------------------------------------ login */
function mostrarLogin(mensagem) {
  pararGeracao(true);
  cancelarGravacao();
  $('#tela-app').hidden = true;
  $('#tela-login').hidden = false;
  const erro = $('#login-erro');
  if (mensagem) {
    erro.textContent = mensagem;
    erro.hidden = false;
  } else {
    erro.hidden = true;
  }
  atualizarCardInstalar();
  setTimeout(() => {
    const u = $('#login-usuario');
    if (!ehMobile()) (u.value ? $('#login-senha') : u).focus();
  }, 50);
}

async function enviarLogin(e) {
  e.preventDefault();
  const usuario = $('#login-usuario').value.trim();
  const senha = $('#login-senha').value;
  const erro = $('#login-erro');
  erro.hidden = true;
  if (!usuario || !senha) {
    erro.textContent = 'Informe usuário e senha.';
    erro.hidden = false;
    return;
  }
  const btn = $('#btn-entrar');
  btn.disabled = true;
  btn.textContent = 'Entrando…';
  try {
    const r = await enviarJSON('/api/auth/login', 'POST', { usuario, senha });
    $('#login-senha').value = '';
    estado.usuario = r.usuario;
    await entrarApp();
  } catch (err) {
    erro.textContent = err.message || 'Não foi possível entrar.';
    erro.hidden = false;
    $('#login-senha').select();
  } finally {
    btn.disabled = false;
    btn.textContent = 'Entrar';
  }
}

async function sair() {
  fecharMenu();
  pararGeracao(true);
  try { await api('/api/auth/logout', { metodo: 'POST' }); } catch { /* já saiu */ }
  estado.usuario = null;
  estado.conversaAtual = null;
  estado.conversas = [];
  estado.agentes = [];
  estado.agenteAtual = null;
  estado.usuarios = [];
  estado.adminAgentes = [];
  conh = { agentes: [], aprova: [], admin: false, max_mb: 15, extensoes: [] };
  $('#menu-enviar').hidden = true;
  $('#menu-aprovacoes').hidden = true;
  history.replaceState(null, '', '/');
  mostrarLogin();
}

/* ------------------------------------------------------------------ aplicação */
async function entrarApp() {
  const u = estado.usuario;
  $('#tela-login').hidden = true;
  $('#tela-app').hidden = false;
  $('#btn-avatar').textContent = iniciais(u.nome, u.username);
  $('#btn-avatar').title = u.nome || u.username;
  $('#menu-nome').textContent = u.nome || u.username;
  $('#menu-email').textContent = u.email || `${u.username}`;
  $('#menu-admin').hidden = u.perfil !== 'admin';
  atualizarSaudacao();

  const params = new URLSearchParams(location.search);
  const rotaInicial = location.pathname;  // mostrarChat() troca o endereço; guarda antes
  const inicial = params.get('c');
  const irParaAdmin = location.pathname === '/admin' && u.perfil === 'admin';
  limparChat();
  if (irParaAdmin) abrirAdmin();
  else mostrarChat();

  await carregarAgentes(Number(params.get('a')) || null);
  await carregarBaseConhecimento();
  if (rotaInicial === '/conhecimento' && (conh.agentes.length || podeAprovar())) abrirConhecimento();
  else if (inicial && !irParaAdmin && estado.agenteAtual) abrirConversa(inicial);
}

function atualizarSaudacao() {
  const h = new Date().getHours();
  const periodo = h < 12 ? 'Bom dia' : h < 18 ? 'Boa tarde' : 'Boa noite';
  const nome = primeiroNome(estado.usuario?.nome, estado.usuario?.username);
  $('#saudacao', boasVindas).textContent = nome ? `${periodo}, ${nome}!` : `${periodo}!`;
}

/* ------------------------------------------------------------------ agentes */
const chaveUltimoAgente = () => `agente-ultimo:${estado.usuario?.username || ''}`;

function lerUltimoAgente() {
  try { return Number(localStorage.getItem(chaveUltimoAgente())) || null; } catch { return null; }
}

function guardarUltimoAgente(id) {
  try { localStorage.setItem(chaveUltimoAgente(), String(id)); } catch { /* sem armazenamento */ }
}

/* Busca os agentes liberados e escolhe qual abrir: o pedido, o atual, o último usado ou o primeiro da lista. */
async function carregarAgentes(preferido = null) {
  try {
    estado.agentes = (await getJSON('/api/agentes')).itens;
  } catch (e) {
    if (e.status !== 401) toast(e.message);
    return;
  }
  const achar = (id) => estado.agentes.find((a) => a.id === id);
  const escolhido = achar(preferido) || achar(estado.agenteAtual?.id) || achar(lerUltimoAgente()) || estado.agentes[0] || null;
  desenharSeletorAgentes();
  if (!escolhido) {
    estado.agenteAtual = null;
    mostrarSemAgente();
    return;
  }
  if (escolhido.id !== estado.agenteAtual?.id) {
    await selecionarAgente(escolhido, { manterConversa: true });
  } else {
    estado.agenteAtual = escolhido;  // nome/ícone podem ter mudado na administração
    aplicarAgenteNaTela();
  }
}

function desenharSeletorAgentes() {
  const sel = $('#sel-agente');
  sel.innerHTML = '';
  for (const a of estado.agentes) {
    const o = document.createElement('option');
    o.value = String(a.id);
    o.textContent = a.nome;
    sel.appendChild(o);
  }
  $('#seletor-agente').hidden = estado.agentes.length === 0;
  sel.disabled = estado.agentes.length < 2;

  const rapidos = $('#agentes-rapidos', boasVindas);
  rapidos.innerHTML = '';
  rapidos.hidden = estado.agentes.length < 2;
  for (const a of estado.agentes) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'agente-rapido';
    b.dataset.id = String(a.id);
    b.title = a.descricao || a.nome;
    b.innerHTML = `${iconeAgente(a)}<span>${esc(a.nome)}</span>`;
    b.addEventListener('click', () => trocarAgente(a.id));
    rapidos.appendChild(b);
  }
}

function aplicarAgenteNaTela() {
  const a = estado.agenteAtual;
  const nome = a?.nome || 'Agente';
  $('#sel-agente').value = a ? String(a.id) : '';
  $('#sel-agente-icone').outerHTML = iconeAgente(a, 'agente-icone', 'sel-agente-icone');
  $('#topo-titulo').textContent = nome;
  document.title = `${nome} · Grupo Flexível`;
  $('#pergunta').placeholder = `Pergunte ao ${nome}…`;
  $('#pergunta').disabled = !a;
  $('#btn-nova').hidden = !a;
  $('#busca-conversa').hidden = !a;
  for (const b of $$('.agente-rapido', boasVindas)) b.classList.toggle('ativo', Number(b.dataset.id) === a?.id);
  desenharAbertura();
  ajustarAltura();
}

function selecionarAgente(a, { manterConversa = false } = {}) {
  cancelarGravacao();  // o áudio seria transcrito pelo agente anterior
  limparAnexos();      // o anexo foi enviado ao app do Dify do agente anterior
  atualizarVoz();
  estado.agenteAtual = a;
  estado.info = null;
  estado.conversas = [];
  guardarUltimoAgente(a.id);
  if (!manterConversa) limparChat();
  aplicarAgenteNaTela();
  atualizarURL();
  desenharConversas();
  carregarInfo();
  return carregarConversas();
}

async function trocarAgente(id) {
  if (id === estado.agenteAtual?.id) return;
  const a = estado.agentes.find((x) => x.id === id);
  if (!a) return;
  pararGeracao(true);
  fecharLateral();
  mostrarChat();
  await selecionarAgente(a);
  if (!ehMobile()) $('#pergunta').focus();
}

/* O admin tirou o acesso (ou desativou o agente) com a tela aberta: recarrega a lista e segue no que sobrou. */
let recarregandoAgentes = null;
function acessoAoAgentePerdido(msg) {
  if (recarregandoAgentes) return recarregandoAgentes;
  toast(msg || 'Você não tem mais acesso a este agente.', 5000);
  pararGeracao(true);
  estado.agenteAtual = null;
  limparChat();
  recarregandoAgentes = carregarAgentes().finally(() => { recarregandoAgentes = null; });
  return recarregandoAgentes;
}

function mostrarSemAgente() {
  pararGeracao(true);
  cancelarGravacao();
  limparChat();
  estado.conversas = [];
  desenharConversas();
  $('#sel-agente-icone').outerHTML = iconeAgente(null, 'agente-icone', 'sel-agente-icone');
  $('#topo-titulo').textContent = 'Sem agente liberado';
  document.title = 'Grupo Flexível';
  $('#pergunta').placeholder = 'Nenhum agente liberado para você';
  $('#pergunta').disabled = true;
  $('#btn-nova').hidden = true;
  $('#busca-conversa').hidden = true;
  $('#abertura', boasVindas).textContent = estado.usuario?.perfil === 'admin'
    ? 'Nenhum agente disponível para você. Cadastre ou marque um agente em Administração > Agentes.'
    : 'Nenhum agente foi liberado para você ainda. Peça a liberação ao administrador.';
  $('#sugestoes', boasVindas).innerHTML = '';
  atualizarBotaoAudio();
  atualizarURL();
  ajustarAltura();
}

function atualizarURL() {
  if (location.pathname === '/admin') return;
  const p = new URLSearchParams();
  if (estado.agenteAtual && estado.agentes.length > 1) p.set('a', String(estado.agenteAtual.id));
  if (estado.conversaAtual) p.set('c', estado.conversaAtual);
  const q = p.toString();
  history.replaceState(null, '', q ? `/?${q}` : '/');
}

async function carregarInfo() {
  const a = estado.agenteAtual;
  if (!a) return;
  let info = null;
  try {
    info = await getJSON(rota('/info', a.id));
  } catch (e) {
    if (e.status === 403 && estado.agenteAtual?.id === a.id) { acessoAoAgentePerdido(e.message); return; }
  }
  if (estado.agenteAtual?.id !== a.id) return;  // trocou de agente enquanto carregava
  estado.info = info;
  desenharAbertura();
  atualizarBotaoAnexo();
  atualizarVoz();
}

function desenharAbertura() {
  const a = estado.agenteAtual;
  if (!a) return;
  const texto = estado.info?.abertura || a.descricao || ABERTURA_PADRAO;
  $('#abertura', boasVindas).textContent = texto.replace(/[*_#`]/g, '').trim();
  desenharSugestoes();
  atualizarBotaoAudio();
}

function desenharSugestoes() {
  const box = $('#sugestoes', boasVindas);
  const lista = (estado.info?.sugestoes || []).filter((s) => s && s.txt);
  box.innerHTML = '';
  for (const s of lista.slice(0, 6)) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'sugestao';
    b.innerHTML = `${s.cat ? `<b>${esc(s.cat)}</b>` : ''}${esc(s.txt)}`;
    b.addEventListener('click', () => {
      $('#pergunta').value = s.txt;
      ajustarAltura();
      enviarPergunta();
    });
    box.appendChild(b);
  }
}

/* ---- menu do usuário ---- */
function abrirMenu() {
  $('#menu-usuario').hidden = false;
  $('#btn-avatar').setAttribute('aria-expanded', 'true');
}
function fecharMenu() {
  $('#menu-usuario').hidden = true;
  $('#btn-avatar').setAttribute('aria-expanded', 'false');
}

/* ---- barra lateral (gaveta no celular) ---- */
function abrirLateral() {
  $('#lateral').classList.add('aberta');
  $('#veu').hidden = false;
}
function fecharLateral() {
  $('#lateral').classList.remove('aberta');
  $('#veu').hidden = true;
}

/* ------------------------------------------------------------------ conversas */
async function carregarConversas(mais = false) {
  const a = estado.agenteAtual;
  if (!a) return;
  try {
    const ultimo = mais && estado.conversas.length ? estado.conversas[estado.conversas.length - 1].id : null;
    const r = await getJSON(rota(`/conversas${ultimo ? `?last_id=${encodeURIComponent(ultimo)}` : ''}`, a.id));
    if (estado.agenteAtual?.id !== a.id) return;  // trocou de agente enquanto carregava
    estado.conversas = mais ? estado.conversas.concat(r.itens) : r.itens;
    estado.maisConversas = r.mais;
  } catch (e) {
    if (e.status === 403 && estado.agenteAtual?.id === a.id) { acessoAoAgentePerdido(e.message); return; }
    if (e.status !== 401) toast(e.message);
  }
  desenharConversas();
}

function grupoDe(ts) {
  if (!ts) return 'Anteriores';
  const d = new Date(ts * 1000);
  const hoje = new Date();
  hoje.setHours(0, 0, 0, 0);
  const dia = 86400000;
  if (d >= hoje) return 'Hoje';
  if (d >= new Date(hoje.getTime() - dia)) return 'Ontem';
  if (d >= new Date(hoje.getTime() - 7 * dia)) return 'Últimos 7 dias';
  if (d >= new Date(hoje.getTime() - 30 * dia)) return 'Últimos 30 dias';
  return 'Anteriores';
}

function desenharConversas() {
  const nav = $('#lista-conversas');
  const filtro = $('#busca-conversa').value.trim().toLowerCase();
  nav.innerHTML = '';
  const lista = estado.conversas.filter((c) => !filtro || (c.nome || '').toLowerCase().includes(filtro));
  if (!lista.length) {
    const vazio = filtro ? 'Nenhuma conversa encontrada.' : estado.agenteAtual ? 'Suas conversas com este agente aparecem aqui.' : '';
    nav.innerHTML = vazio ? `<p class="muted" style="padding:8px 10px;font-size:14px">${vazio}</p>` : '';
  }
  let grupoAtual = '';
  for (const c of lista) {
    const g = grupoDe(c.atualizado);
    if (g !== grupoAtual) {
      grupoAtual = g;
      const h = document.createElement('div');
      h.className = 'grupo-data';
      h.textContent = g;
      nav.appendChild(h);
    }
    const item = document.createElement('div');
    item.className = `conversa${c.id === estado.conversaAtual ? ' ativa' : ''}`;
    item.dataset.id = c.id;
    item.innerHTML = `
      <span class="conversa-nome" title="${esc(c.nome)}">${esc(c.nome)}</span>
      <button class="btn-icone" data-acao="renomear" aria-label="Renomear" title="Renomear">${ICONE.editar}</button>
      <button class="btn-icone" data-acao="apagar" aria-label="Apagar" title="Apagar">${ICONE.lixo}</button>`;
    item.addEventListener('click', (ev) => {
      const acao = ev.target.closest('button')?.dataset.acao;
      if (acao === 'renomear') renomearConversa(c);
      else if (acao === 'apagar') apagarConversa(c);
      else {
        mostrarChat();
        abrirConversa(c.id);
      }
    });
    nav.appendChild(item);
  }
  $('#btn-mais-conversas').hidden = !estado.maisConversas || !!filtro;
}

function marcarConversaAtiva() {
  for (const el of $$('.conversa')) el.classList.toggle('ativa', el.dataset.id === estado.conversaAtual);
}

async function renomearConversa(c) {
  const nome = await confirmar({ titulo: 'Renomear conversa', texto: '', input: true, valor: c.nome, ok: 'Salvar' });
  if (nome === null) return;
  try {
    const r = await enviarJSON(rota(`/conversas/${encodeURIComponent(c.id)}`), 'PATCH', { nome: nome.trim() });
    c.nome = r.nome || nome;
    desenharConversas();
  } catch (e) {
    toast(e.message);
  }
}

async function apagarConversa(c) {
  const ok = await confirmar({ titulo: 'Apagar conversa?', texto: `"${c.nome}" será apagada do seu histórico.`, ok: 'Apagar' });
  if (ok === null) return;
  try {
    await api(rota(`/conversas/${encodeURIComponent(c.id)}`), { metodo: 'DELETE' });
    estado.conversas = estado.conversas.filter((x) => x.id !== c.id);
    if (estado.conversaAtual === c.id) novaConversa();
    desenharConversas();
    toast('Conversa apagada.');
  } catch (e) {
    toast(e.message);
  }
}

function limparChat() {
  pararLeitura();
  estado.conversaAtual = null;
  estado.sessaoChat += 1;
  const box = $('#mensagens');
  box.innerHTML = '';
  box.appendChild(boasVindas);
  atualizarSaudacao();
  marcarConversaAtiva();
}

function novaConversa(focar = true) {
  pararGeracao(true);
  limparChat();
  atualizarURL();
  fecharLateral();
  mostrarChat();
  if (focar && !ehMobile()) $('#pergunta').focus();
}

async function abrirConversa(id, primeiroId = null) {
  pararGeracao(true);
  if (!primeiroId) pararLeitura();
  fecharLateral();
  const box = $('#mensagens');
  const a = estado.agenteAtual;
  if (!a) return;
  if (!primeiroId) {
    estado.conversaAtual = id;
    estado.sessaoChat += 1;
    atualizarURL();
    marcarConversaAtiva();
    box.innerHTML = '<div class="msg msg-bot"><div class="conteudo"><span class="status"><span class="spinner"></span>Carregando a conversa…</span></div></div>';
  }
  const sessao = estado.sessaoChat;
  try {
    const r = await getJSON(rota(`/conversas/${encodeURIComponent(id)}/mensagens${primeiroId ? `?first_id=${encodeURIComponent(primeiroId)}` : ''}`, a.id));
    if (estado.sessaoChat !== sessao || estado.conversaAtual !== id) return;
    const itens = [...r.itens].sort((a, b) => (a.criado || 0) - (b.criado || 0));
    const frag = document.createDocumentFragment();
    if (r.mais && itens.length) {
      const b = document.createElement('button');
      b.className = 'btn btn-texto';
      b.style.display = 'block';
      b.textContent = 'Ver mensagens anteriores';
      b.addEventListener('click', () => { b.remove(); abrirConversa(id, itens[0].id); });
      frag.appendChild(b);
    }
    for (const m of itens) {
      frag.appendChild(bolhaUsuario(m.pergunta, m.anexos || []));
      const bot = bolhaBot();
      renderMarkdown(bot.md, m.resposta);
      finalizarBot(bot, { id: m.id, texto: m.resposta, avaliacao: m.avaliacao, fontes: m.fontes, aid: a.id, cid: id });
      frag.appendChild(bot.raiz);
    }
    if (primeiroId) {
      box.prepend(frag);
    } else {
      box.innerHTML = '';
      if (!itens.length) box.appendChild(boasVindas);
      box.appendChild(frag);
      rolarFim(true);
    }
  } catch (e) {
    if (e.status === 401 || estado.sessaoChat !== sessao) return;
    if (e.status === 403) { acessoAoAgentePerdido(e.message); return; }
    if (!primeiroId) {
      box.innerHTML = '';
      box.appendChild(erroBox(e.status === 404 ? 'Conversa não encontrada.' : e.message));
    } else {
      toast(e.message);
    }
  }
}

/* ------------------------------------------------------------------ mensagens */
let boasVindas;

function bolhaUsuario(texto, anexos = []) {
  const d = document.createElement('div');
  d.className = 'msg msg-user';
  if (anexos.length) {
    const l = document.createElement('div');
    l.className = 'anexos-msg';
    for (const a of anexos) l.appendChild(chipAnexo(a));
    d.appendChild(l);
  }
  const b = document.createElement('div');
  b.className = 'bolha';
  b.textContent = texto;
  d.appendChild(b);
  return d;
}

function chipAnexo(a) {
  const c = document.createElement('span');
  c.className = 'chip-anexo';
  c.innerHTML = `<span class="chip-ico" aria-hidden="true">${a.tipo === 'imagem' ? '🖼️' : '📄'}</span><span class="chip-nome"></span>`;
  $('.chip-nome', c).textContent = a.nome;
  c.title = a.nome;
  return c;
}

function bolhaBot() {
  const raiz = document.createElement('div');
  raiz.className = 'msg msg-bot';
  raiz.innerHTML = '<img class="avatar-bot" src="/icons/icon-192.png" alt="" width="30" height="30"><div class="conteudo"><div class="md"></div></div>';
  return { raiz, conteudo: $('.conteudo', raiz), md: $('.md', raiz) };
}

function erroBox(msg) {
  const d = document.createElement('div');
  d.className = 'msg-erro';
  d.textContent = msg;
  return d;
}

function finalizarBot(bot, { id, texto, avaliacao, fontes, aid, cid }) {
  if (fontes && fontes.length) {
    const det = document.createElement('details');
    det.className = 'fontes';
    det.innerHTML = `<summary>Fontes consultadas (${fontes.length})</summary><ul>${fontes
      .map((f) => `<li>${esc(f.documento)}${f.base ? ` <span class="muted">· ${esc(f.base)}</span>` : ''}</li>`)
      .join('')}</ul>`;
    bot.conteudo.appendChild(det);
  }
  if (!texto) return;
  const acoes = document.createElement('div');
  acoes.className = 'msg-acoes';
  acoes.innerHTML = `
    <button class="btn-icone" data-a="copiar" aria-label="Copiar resposta" title="Copiar">${ICONE.copiar}</button>
    <button class="btn-icone btn-ouvir" data-a="ouvir" aria-label="Ouvir resposta" title="Ouvir">${ICONE.ouvir}</button>
    ${id ? `<button class="btn-icone${avaliacao === 'like' ? ' on' : ''}" data-a="like" aria-label="Resposta útil" title="Resposta útil">${ICONE.like}</button>
    <button class="btn-icone${avaliacao === 'dislike' ? ' on' : ''}" data-a="dislike" aria-label="Resposta ruim" title="Resposta ruim">${ICONE.dislike}</button>` : ''}`;
  acoes.addEventListener('click', async (ev) => {
    const b = ev.target.closest('button');
    if (!b) return;
    if (b.dataset.a === 'ouvir') {
      alternarLeitura(b, aid, texto);
      return;
    }
    if (b.dataset.a === 'copiar') {
      if (await copiarTexto(texto)) {
        b.innerHTML = ICONE.ok;
        setTimeout(() => { b.innerHTML = ICONE.copiar; }, 1500);
      } else {
        toast('Não foi possível copiar.');
      }
      return;
    }
    const ligado = b.classList.contains('on');
    const nova = ligado ? null : b.dataset.a;
    const recebe = !!estado.agentes.find((a) => a.id === aid)?.recebe_conhecimento;
    try {
      const r = await enviarJSON(rota(`/mensagens/${encodeURIComponent(id)}/feedback`, aid), 'POST',
        { avaliacao: nova, conversation_id: cid || null });
      for (const x of $$('[data-a="like"],[data-a="dislike"]', acoes)) x.classList.toggle('on', x.dataset.a === nova);
      if (r.aviso) toast(r.aviso, 6000);
      else if (nova === 'like') toast(r.envio ? 'Obrigado! A resposta foi para aprovação e pode virar conhecimento do agente.' : 'Obrigado pela avaliação!', 5000);
      else if (nova === 'dislike' && !r.envio) toast('Obrigado! Vamos usar isso para melhorar o agente.');
      // 👎 já foi para aprovação; a correção é opcional (sem ela, o aprovador escreve a resposta certa)
      if (nova === 'dislike' && r.envio && recebe) abrirCorrecao({ aid, id, cid, texto });
    } catch (e) {
      toast(e.message);
    }
  });
  bot.conteudo.appendChild(acoes);
}

/* ------------------------------------------------------------------ ouvir a resposta (texto para voz do Dify) */
/* Uma leitura por vez: o servidor limpa o texto (tabelas e símbolos não são lidos) e o Dify gera o áudio. */
let leitura = null;  // { botao, audio, url, ctrl }

/* o botão vai em toda resposta e só aparece quando o agente tem "Texto para voz" (a configuração pode chegar
   depois do histórico) */
function atualizarVoz() {
  $('#mensagens').classList.toggle('com-voz', !!estado.info?.voz?.ativo);
  if (!estado.info?.voz?.ativo) pararLeitura();
}

function pararLeitura() {
  if (!leitura) return;
  const { botao, audio, url, ctrl } = leitura;
  leitura = null;
  ctrl.abort();
  if (audio) { audio.pause(); audio.removeAttribute('src'); }
  if (url) URL.revokeObjectURL(url);
  botao.classList.remove('on', 'carregando');
  botao.innerHTML = ICONE.ouvir;
  botao.setAttribute('aria-label', 'Ouvir resposta');
  botao.title = 'Ouvir';
}

async function alternarLeitura(botao, aid, texto, automatica = false) {
  const mesma = leitura?.botao === botao;
  pararLeitura();
  if (mesma) return;
  const atual = { botao, audio: null, url: null, ctrl: new AbortController() };
  leitura = atual;
  botao.classList.add('on', 'carregando');
  botao.innerHTML = '<span class="spinner" aria-hidden="true"></span>';
  botao.setAttribute('aria-label', 'Parar leitura');
  botao.title = 'Parar';
  try {
    const r = await api(rota('/ouvir', aid), { metodo: 'POST', corpo: { texto }, sinal: atual.ctrl.signal });
    const blob = await r.blob();
    if (leitura !== atual) return;
    atual.url = URL.createObjectURL(blob);
    atual.audio = new Audio(atual.url);
    atual.audio.addEventListener('ended', () => { if (leitura === atual) pararLeitura(); });
    botao.classList.remove('carregando');
    botao.innerHTML = ICONE.parar;
    await atual.audio.play();
  } catch (e) {
    if (leitura !== atual || e.name === 'AbortError') return;
    pararLeitura();
    // o navegador pode bloquear o som automático; aí a pessoa toca no botão
    if (automatica && e.name === 'NotAllowedError') return;
    toast(e.message || 'Não foi possível tocar o áudio.', 5000);
  }
}

let seguirFim = true;
function rolarFim(forcar = false) {
  const box = $('#mensagens');
  if (forcar || seguirFim) box.scrollTop = box.scrollHeight;
}

/* ------------------------------------------------------------------ anexos (recurso "Upload de Arquivo" do Dify) */
/* Cada arquivo sobe assim que é escolhido (vai direto para o Dify) e entra na próxima pergunta. */
let anexos = [];  // { nome, tipo, id, situacao: 'enviando'|'ok'|'erro', erro, ctrl, aid }

function cfgAnexos() {
  return estado.info?.anexos?.ativo ? estado.info.anexos : null;
}

function atualizarBotaoAnexo() {
  const cfg = cfgAnexos();
  $('#btn-anexar').hidden = !cfg || !estado.agenteAtual || !!gravacao;
  $('#btn-anexar').disabled = estado.gerando || (cfg && anexos.length >= cfg.max);
  if (cfg) $('#anexo-input').accept = cfg.extensoes.map((e) => `.${e}`).join(',');
}

function desenharAnexos() {
  const fila = $('#anexos-fila');
  fila.innerHTML = '';
  fila.hidden = !anexos.length;
  for (const a of anexos) {
    const c = chipAnexo(a);
    c.classList.add(a.situacao);
    if (a.situacao === 'enviando') c.insertAdjacentHTML('afterbegin', '<span class="spinner" aria-hidden="true"></span>');
    if (a.situacao === 'erro') {
      c.title = a.erro;
      c.insertAdjacentHTML('beforeend', `<span class="chip-erro"></span>`);
      $('.chip-erro', c).textContent = a.erro;
    }
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'chip-tirar';
    x.setAttribute('aria-label', `Tirar ${a.nome}`);
    x.title = 'Tirar';
    x.textContent = '×';
    x.addEventListener('click', () => tirarAnexo(a));
    c.appendChild(x);
    fila.appendChild(c);
  }
  atualizarBotaoAnexo();
  atualizarBotaoEnviar();
}

function tirarAnexo(a) {
  a.ctrl?.abort();
  anexos = anexos.filter((x) => x !== a);
  desenharAnexos();
}

function limparAnexos() {
  for (const a of anexos) a.ctrl?.abort();
  anexos = [];
  desenharAnexos();
}

function adicionarAnexos(arquivos) {
  const cfg = cfgAnexos();
  const agente = estado.agenteAtual;
  if (!cfg || !agente) return;
  for (const f of arquivos) {
    if (anexos.length >= cfg.max) {
      toast(`Este agente aceita até ${cfg.max} anexo${cfg.max === 1 ? '' : 's'} por pergunta.`, 5000);
      break;
    }
    const ext = (f.name.includes('.') ? f.name.split('.').pop() : '').toLowerCase();
    if (!cfg.extensoes.includes(ext)) {
      toast(`Arquivo .${ext || '?'} não aceito. Aceitos: ${cfg.extensoes.map((e) => `.${e}`).join(', ')}.`, 6000);
      continue;
    }
    const imagem = ['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext);
    const mb = imagem ? cfg.max_mb_imagem : cfg.max_mb;
    if (f.size > mb * 1024 * 1024) {
      toast(`"${f.name}" passou de ${mb} MB.`, 5000);
      continue;
    }
    if (!f.size) {
      toast(`"${f.name}" está vazio.`);
      continue;
    }
    const a = { nome: f.name, tipo: imagem ? 'imagem' : 'documento', id: null, situacao: 'enviando', ctrl: new AbortController(), aid: agente.id };
    anexos.push(a);
    enviarAnexo(a, f);
  }
  desenharAnexos();
}

async function enviarAnexo(a, arquivo) {
  try {
    const r = await (await api(rota('/arquivos', a.aid), {
      metodo: 'POST', bruto: new Blob([arquivo], { type: 'application/octet-stream' }), sinal: a.ctrl.signal,
      cabecalhos: { 'X-Arquivo-Nome': encodeURIComponent(arquivo.name) },
    })).json();
    Object.assign(a, { id: r.id, nome: r.nome, tipo: r.tipo, situacao: 'ok' });
  } catch (e) {
    if (e.name === 'AbortError') return;
    Object.assign(a, { situacao: 'erro', erro: e.message });
  }
  a.ctrl = null;
  if (anexos.includes(a)) desenharAnexos();
}

/* ------------------------------------------------------------------ áudio (voz para texto) */
/* Grava no navegador, converte para WAV 16 kHz mono (o Dify não aceita o webm que o Chrome grava),
   manda para o servidor transcrever com o modelo do agente e põe o texto na caixa para a pessoa revisar. */
const AUDIO_MAX_SEG = 300;        // 5 min ≈ 9,6 MB em WAV 16 kHz (servidor aceita até AUDIO_MAX_MB)
const AUDIO_MIN_SEG = 0.7;
let gravacao = null;              // { rec, stream, partes, inicio, timer, cancelada, aid, abort }

function audioSuportado() {
  return window.isSecureContext && !!navigator.mediaDevices?.getUserMedia && 'MediaRecorder' in window
    && !!(window.AudioContext || window.webkitAudioContext) && 'OfflineAudioContext' in window;
}

function atualizarBotaoAudio() {
  const pode = audioSuportado() && !!estado.agenteAtual && !!estado.info?.audio;
  $('#btn-microfone').hidden = !pode || !!gravacao;
  $('#btn-microfone').disabled = estado.gerando;
  atualizarBotaoAnexo();
}

function tempoGravacao(seg) {
  const s = Math.floor(seg);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function modoGravacao(fase) {  // null | 'gravando' | 'transcrevendo'
  const caixa = $('#compositor-caixa');
  caixa.classList.toggle('em-gravacao', !!fase);
  $('#gravando').hidden = !fase;
  $('#pergunta').hidden = !!fase;
  $('#btn-enviar').hidden = !!fase || estado.gerando;
  $('#btn-concluir').hidden = fase !== 'gravando';
  $('#gravando').classList.toggle('transcrevendo', fase === 'transcrevendo');
  $('#grav-texto').textContent = fase === 'transcrevendo' ? 'Transcrevendo o áudio…' : 'Gravando… fale a sua pergunta';
  if (!fase) $('#grav-tempo').textContent = '0:00';
  atualizarBotaoAudio();
}

async function iniciarGravacao() {
  if (gravacao || estado.gerando || !estado.agenteAtual) return;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
  } catch (e) {
    toast(e.name === 'NotAllowedError' ? 'Permita o uso do microfone no navegador (ícone do cadeado na barra de endereço) para enviar áudio.'
      : e.name === 'NotFoundError' ? 'Nenhum microfone encontrado neste aparelho.'
        : 'Não foi possível usar o microfone.', 6000);
    return;
  }
  let rec;
  try {
    rec = new MediaRecorder(stream);
  } catch {
    stream.getTracks().forEach((t) => t.stop());
    toast('Este navegador não consegue gravar áudio.');
    return;
  }
  const g = { rec, stream, partes: [], inicio: performance.now(), cancelada: false, aid: estado.agenteAtual.id, abort: null };
  gravacao = g;
  rec.ondataavailable = (ev) => { if (ev.data && ev.data.size) g.partes.push(ev.data); };
  rec.onstop = () => finalizarGravacao(g);
  rec.start(1000);
  g.timer = setInterval(() => {
    const seg = (performance.now() - g.inicio) / 1000;
    $('#grav-tempo').textContent = tempoGravacao(seg);
    if (seg >= AUDIO_MAX_SEG) {
      toast('Limite de 5 minutos atingido: transcrevendo o que foi gravado.');
      concluirGravacao();
    }
  }, 250);
  modoGravacao('gravando');
}

function soltarMicrofone(g) {
  clearInterval(g.timer);
  g.stream.getTracks().forEach((t) => t.stop());
}

function concluirGravacao() {
  const g = gravacao;
  if (!g || g.rec.state === 'inactive') return;
  g.segundos = (performance.now() - g.inicio) / 1000;
  soltarMicrofone(g);
  g.rec.stop();  // dispara onstop -> finalizarGravacao
}

/* descarta a gravação (ou a transcrição em andamento); também usada ao trocar de conversa/agente e ao sair */
function cancelarGravacao() {
  const g = gravacao;
  if (!g) return;
  g.cancelada = true;
  gravacao = null;
  soltarMicrofone(g);
  if (g.rec.state !== 'inactive') g.rec.stop();
  g.abort?.abort();
  modoGravacao(null);
}

async function finalizarGravacao(g) {
  if (g.cancelada) return;
  if ((g.segundos || 0) < AUDIO_MIN_SEG || !g.partes.length) {
    gravacao = null;
    modoGravacao(null);
    toast('Áudio curto demais. Toque no microfone, fale e depois toque em ✓.');
    return;
  }
  modoGravacao('transcrevendo');
  try {
    const wav = await paraWav16k(new Blob(g.partes, { type: g.rec.mimeType || 'audio/webm' }));
    if (g.cancelada) return;
    g.abort = new AbortController();
    const r = await (await api(rota('/audio', g.aid), { metodo: 'POST', bruto: wav, sinal: g.abort.signal })).json();
    if (g.cancelada || estado.agenteAtual?.id !== g.aid) return;
    const ta = $('#pergunta');
    ta.value = ta.value.trim() ? `${ta.value.trimEnd()} ${r.texto}` : r.texto;
    gravacao = null;
    modoGravacao(null);
    ajustarAltura();
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    toast('Confira o texto e toque em enviar.');
  } catch (e) {
    if (g.cancelada || e.name === 'AbortError') return;
    gravacao = null;
    modoGravacao(null);
    if (e.status === 401) return;
    if (e.status === 403) { acessoAoAgentePerdido(e.message); return; }
    toast(e.status === 413 && e.message === 'Erro 413'
      ? 'O servidor recusou o áudio por tamanho. Grave um trecho menor (ou peça à TI para ajustar o nginx).'
      : e instanceof ErroApi ? e.message : 'Não foi possível ler o áudio gravado.', 6000);
  }
}

/* decodifica o que o navegador gravou (webm/opus, mp4/aac…) e regrava em WAV PCM 16 bits, 16 kHz, mono */
async function paraWav16k(blob, taxa = 16000) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  let audio;
  try {
    audio = await ctx.decodeAudioData(await blob.arrayBuffer());
  } finally {
    ctx.close?.();
  }
  const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(audio.duration * taxa)), taxa);
  const fonte = off.createBufferSource();
  fonte.buffer = audio;
  fonte.connect(off.destination);  // mistura os canais em mono
  fonte.start();
  const pcm = (await off.startRendering()).getChannelData(0);

  const dv = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const txt = (pos, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(pos + i, s.charCodeAt(i)); };
  txt(0, 'RIFF');
  dv.setUint32(4, 36 + pcm.length * 2, true);
  txt(8, 'WAVE');
  txt(12, 'fmt ');
  dv.setUint32(16, 16, true);         // tamanho do bloco fmt
  dv.setUint16(20, 1, true);          // PCM
  dv.setUint16(22, 1, true);          // mono
  dv.setUint32(24, taxa, true);
  dv.setUint32(28, taxa * 2, true);   // bytes por segundo
  dv.setUint16(32, 2, true);          // bytes por amostra
  dv.setUint16(34, 16, true);         // bits por amostra
  txt(36, 'data');
  dv.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const v = Math.max(-1, Math.min(1, pcm[i]));
    dv.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  return new Blob([dv], { type: 'audio/wav' });
}

/* ------------------------------------------------------------------ envio + streaming */
function ajustarAltura() {
  const ta = $('#pergunta');
  ta.style.height = 'auto';
  // vazio ou ainda oculto: deixa a altura natural (rows=1) para não cortar o placeholder
  if (ta.value && ta.scrollHeight) ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  atualizarBotaoEnviar();
}

function atualizarBotaoEnviar() {
  // com anexo pronto dá para enviar sem texto (vai uma pergunta padrão); anexo subindo segura o envio
  const temAnexo = anexos.some((a) => a.situacao === 'ok');
  $('#btn-enviar').disabled = (!$('#pergunta').value.trim() && !temAnexo) || estado.gerando || !estado.agenteAtual
    || anexos.some((a) => a.situacao === 'enviando');
}

function modoGerando(sim) {
  estado.gerando = sim;
  $('#btn-enviar').hidden = sim;
  $('#btn-parar').hidden = !sim;
  atualizarBotaoAudio();
  ajustarAltura();
}

async function pararGeracao(silencioso = false) {
  if (!estado.gerando) return;
  const task = estado.taskId;
  const ctrl = estado.abort;
  const aid = estado.geracaoAgente;
  if (silencioso) ctrl?.abort();  // troca de conversa/agente: corta já e avisa o Dify em segundo plano
  if (task && aid) {
    const pedido = api(rota(`/chat/${encodeURIComponent(task)}/parar`, aid), { metodo: 'POST' }).catch(() => { /* segue */ });
    if (!silencioso) await pedido;
  }
  // se o Dify não encerrar o stream sozinho, corta do lado de cá
  if (!silencioso) setTimeout(() => ctrl?.abort(), 2500);
}

async function enviarPergunta() {
  const ta = $('#pergunta');
  const agente = estado.agenteAtual;
  if (estado.gerando || !agente) return;
  if (anexos.some((a) => a.situacao === 'enviando')) { toast('Aguarde o anexo terminar de enviar.'); return; }
  if (anexos.some((a) => a.situacao === 'erro')) { toast('Tire o anexo com erro antes de enviar.'); return; }
  const enviados = anexos.filter((a) => a.situacao === 'ok' && a.aid === agente.id);
  const pergunta = ta.value.trim() || (enviados.length ? `Analise ${enviados.length > 1 ? 'os arquivos anexados' : 'o arquivo anexado'}.` : '');
  if (!pergunta) return;
  anexos = [];
  desenharAnexos();
  ta.value = '';
  ajustarAltura();
  if (ehMobile()) ta.blur();

  const box = $('#mensagens');
  if (boasVindas.isConnected) boasVindas.remove();
  box.appendChild(bolhaUsuario(pergunta, enviados));
  const bot = bolhaBot();
  const status = document.createElement('div');
  status.className = 'status';
  status.innerHTML = '<span class="spinner"></span><span>Pensando…</span>';
  bot.conteudo.prepend(status);
  box.appendChild(bot.raiz);
  seguirFim = true;
  rolarFim(true);

  const ctrl = new AbortController();
  estado.abort = ctrl;
  estado.taskId = null;
  estado.geracaoAgente = agente.id;
  modoGerando(true);

  const conversaEnviada = estado.conversaAtual;
  let conversaId = conversaEnviada;  // conversa desta resposta (para o 👍/👎 virar conhecimento)
  const sessao = estado.sessaoChat;
  const mesmaTela = () => estado.sessaoChat === sessao;
  let texto = '';
  let messageId = null;
  let fontes = [];
  let terminou = false;
  let erro = null;
  let agendado = false;
  let ultimoStatus = 'Pensando…';

  const desenhar = () => {
    agendado = false;
    renderMarkdown(bot.md, texto, true);
    rolarFim();
  };
  const agendar = () => {
    if (!agendado) {
      agendado = true;
      requestAnimationFrame(desenhar);
    }
  };
  const setStatus = (v) => {
    if (v === ultimoStatus) return;
    ultimoStatus = v;
    status.lastElementChild.textContent = v;
  };

  try {
    const r = await api(rota('/chat', agente.id), { metodo: 'POST', corpo: { pergunta, conversation_id: conversaEnviada, arquivos: enviados.map((a) => a.id) }, sinal: ctrl.signal });
    const leitor = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await leitor.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const bloco = buf.slice(0, i);
        buf = buf.slice(i + 2);
        for (const linha of bloco.split('\n')) {
          if (!linha.startsWith('data:')) continue;
          let ev;
          try { ev = JSON.parse(linha.slice(5)); } catch { continue; }
          switch (ev.t) {
            case 'inicio':
              estado.taskId = ev.task_id;
              conversaId = ev.conversation_id || conversaId;
              messageId = ev.message_id || messageId;
              if (mesmaTela() && !estado.conversaAtual && ev.conversation_id) {
                estado.conversaAtual = ev.conversation_id;
                atualizarURL();
              }
              break;
            case 'texto':
              texto += ev.v;
              setStatus('Redigindo a resposta');
              agendar();
              break;
            case 'substituir':
              texto = ev.v || '';
              agendar();
              break;
            case 'status':
              setStatus(ev.v);
              break;
            case 'fim':
              terminou = true;
              messageId = ev.message_id || messageId;
              fontes = ev.fontes || [];
              break;
            case 'erro':
              erro = ev.v;
              break;
            default:
              break;
          }
        }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') erro = e.status === 401 ? null : e.message;
    if (e.status === 403 && mesmaTela()) acessoAoAgentePerdido(e.message);
  }

  status.remove();
  renderMarkdown(bot.md, texto);
  if (!texto && !erro) {
    bot.md.innerHTML = `<p class="muted">${terminou ? 'O agente não retornou texto.' : 'Resposta interrompida.'}</p>`;
  } else if (!terminou && !erro && texto) {
    bot.md.insertAdjacentHTML('beforeend', '<p class="muted"><em>(resposta interrompida)</em></p>');
  }
  if (erro) bot.conteudo.appendChild(erroBox(erro));
  finalizarBot(bot, { id: messageId, texto, fontes, aid: agente.id, cid: conversaId });
  if (terminou && !erro && texto && mesmaTela() && estado.info?.voz?.automatica) {
    const b = $('[data-a="ouvir"]', bot.conteudo);
    if (b) alternarLeitura(b, agente.id, texto, true);
  }
  // se outra resposta já começou (troca rápida de conversa), não mexe no estado dela
  if (estado.abort === ctrl) {
    estado.taskId = null;
    estado.abort = null;
    estado.geracaoAgente = null;
    modoGerando(false);
  }
  if (!mesmaTela()) return;
  rolarFim();

  if (estado.usuario && estado.agenteAtual?.id === agente.id) {
    const nova = !conversaEnviada && estado.conversaAtual;
    await carregarConversas();
    marcarConversaAtiva();
    // o nome automático da conversa costuma chegar um pouco depois
    if (nova) setTimeout(() => { if (estado.agenteAtual?.id === agente.id) carregarConversas(); }, 3500);
  }
}

/* ------------------------------------------------------------------ navegação chat/admin */
function mostrarChat() {
  $('#view-admin').hidden = true;
  $('#view-conhecimento').hidden = true;
  clearTimeout(timerMeusEnvios);
  $('#view-chat').hidden = false;
  $('#lateral').hidden = false;
  if (location.pathname === '/admin' || location.pathname === '/conhecimento') {
    history.replaceState(null, '', '/');
    atualizarURL();
  }
}

function abrirAdmin() {
  if (estado.usuario?.perfil !== 'admin') return;
  fecharMenu();
  fecharLateral();
  $('#view-chat').hidden = true;
  $('#lateral').hidden = true;
  $('#view-conhecimento').hidden = true;
  $('#view-admin').hidden = false;
  history.replaceState(null, '', '/admin');
  trocarAba('usuarios');
}

function trocarAba(aba) {
  for (const b of $$('.aba')) {
    const ativa = b.dataset.aba === aba;
    b.classList.toggle('ativa', ativa);
    b.setAttribute('aria-selected', String(ativa));
  }
  $('#aba-usuarios').hidden = aba !== 'usuarios';
  $('#aba-agentes').hidden = aba !== 'agentes';
  $('#aba-conversas').hidden = aba !== 'conversas';
  $('#aba-acessos').hidden = aba !== 'acessos';
  if (aba === 'usuarios') carregarUsuarios();
  else if (aba === 'agentes') carregarAgentesAdmin();
  else if (aba === 'conversas') abrirConversasAdmin();
  else if (aba === 'acessos') abrirRegistros();
}

/* ------------------------------------------------------------------ administração */
async function carregarUsuarios() {
  const lista = $('#lista-usuarios');
  if (!estado.usuarios.length) lista.innerHTML = '<p class="muted">Carregando…</p>';
  try {
    const [usuarios, agentes] = await Promise.all([getJSON('/api/admin/usuarios'), getJSON('/api/admin/agentes')]);
    estado.usuarios = usuarios.itens;
    estado.adminAgentes = agentes.itens;
  } catch (e) {
    lista.innerHTML = '';
    lista.appendChild(erroBox(e.message));
    return;
  }
  desenharUsuarios();
}

function desenharUsuarios() {
  const lista = $('#lista-usuarios');
  const filtro = $('#filtro-usuarios').value.trim().toLowerCase();
  const itens = estado.usuarios.filter((u) => !filtro
    || [u.username, u.nome, u.email, u.departamento, u.cargo].some((v) => (v || '').toLowerCase().includes(filtro)));
  lista.innerHTML = '';
  if (!itens.length) {
    lista.innerHTML = `<p class="muted">${filtro ? 'Ninguém encontrado com esse filtro.' : 'Nenhum usuário cadastrado.'}</p>`;
    return;
  }
  for (const u of itens) {
    const eu = u.username === estado.usuario.username;
    const linha = document.createElement('div');
    linha.className = `usuario${u.ativo ? '' : ' inativo'}`;
    linha.innerHTML = `
      <div class="ini">${esc(iniciais(u.nome, u.username))}</div>
      <div class="info">
        <div class="nome">${esc(u.nome || u.username)}${u.perfil === 'admin' ? '<span class="selo">ADMIN</span>' : ''}${eu ? ' <span class="muted" style="font-weight:400">(você)</span>' : ''}</div>
        <div class="sub">${esc(u.username)}${u.email ? ` · ${esc(u.email)}` : ''}</div>
      </div>
      <div class="setor">
        <div class="sub">${esc(u.departamento || '—')}${u.cargo ? ` · ${esc(u.cargo)}` : ''}</div>
        <div class="sub">Último acesso: ${esc(dataHora(u.ultimo_acesso))} · ${Number(u.qtd_acessos || 0)}×</div>
      </div>
      ${botaoAgentesUsuario(u)}
      ${botaoAprovaUsuario(u)}
      <select aria-label="Perfil de ${esc(u.username)}">
        <option value="usuario"${u.perfil === 'usuario' ? ' selected' : ''}>Usuário</option>
        <option value="admin"${u.perfil === 'admin' ? ' selected' : ''}>Administrador</option>
      </select>
      <label class="chave"><input type="checkbox"${u.ativo ? ' checked' : ''}><span>${u.ativo ? 'Ativo' : 'Inativo'}</span></label>
      <button class="btn-icone rem" aria-label="Remover ${esc(u.username)}" title="Remover acesso">${ICONE.lixo}</button>`;

    $('.btn-agentes:not(.btn-aprova)', linha).addEventListener('click', () => abrirAgentesUsuario(u));
    $('.btn-aprova', linha).addEventListener('click', () => abrirAgentesUsuario(u, 'aprova'));
    $('select', linha).addEventListener('change', async (ev) => {
      const perfil = ev.target.value;
      if (eu && perfil !== 'admin') {
        const ok = await confirmar({ titulo: 'Deixar de ser administrador?', texto: 'Você perderá acesso a esta tela.', ok: 'Confirmar' });
        if (ok === null) { ev.target.value = u.perfil; return; }
      }
      await alterarUsuario(u, { perfil }, () => { ev.target.value = u.perfil; });
    });
    $('input[type=checkbox]', linha).addEventListener('change', async (ev) => {
      const ativo = ev.target.checked;
      if (!ativo) {
        const ok = await confirmar({ titulo: 'Desativar acesso?', texto: `${u.nome || u.username} não conseguirá mais entrar em nenhum agente (o histórico é mantido).`, ok: 'Desativar' });
        if (ok === null) { ev.target.checked = true; return; }
      }
      await alterarUsuario(u, { ativo }, () => { ev.target.checked = !ativo; });
    });
    $('.rem', linha).addEventListener('click', async () => {
      const ok = await confirmar({ titulo: 'Remover usuário?', texto: `${u.nome || u.username} sai da lista e perde o acesso. Pode ser incluído de novo depois.`, ok: 'Remover' });
      if (ok === null) return;
      try {
        await api(`/api/admin/usuarios/${encodeURIComponent(u.username)}`, { metodo: 'DELETE' });
        toast('Usuário removido.');
        if (eu) { sair(); return; }
        carregarUsuarios();
      } catch (e) {
        toast(e.message);
      }
    });
    lista.appendChild(linha);
  }
}

async function alterarUsuario(u, campos, desfazer) {
  try {
    await enviarJSON(`/api/admin/usuarios/${encodeURIComponent(u.username)}`, 'PATCH', campos);
    toast(campos.perfil === 'admin' ? 'Alteração salva. Administradores recebem todos os agentes.' : 'Alteração salva.');
    if (u.username === estado.usuario.username && campos.perfil) {
      estado.usuario.perfil = campos.perfil;
      $('#menu-admin').hidden = campos.perfil !== 'admin';
      if (campos.perfil !== 'admin') { mostrarChat(); return; }
    }
    carregarUsuarios();
  } catch (e) {
    desfazer();
    toast(e.message, 4500);
  }
}

/* ---- agentes de cada usuário ---- */
function botaoAgentesUsuario(u) {
  const total = estado.adminAgentes.length;
  const nomes = estado.adminAgentes.filter((a) => u.agentes.includes(a.id)).map((a) => a.nome);
  const texto = !total ? 'Sem agentes cadastrados'
    : !nomes.length ? 'Nenhum agente'
      : nomes.length === total ? `Todos os agentes (${total})`
        : nomes.length === 1 ? nomes[0] : `${nomes.length} de ${total} agentes`;
  return `<button type="button" class="btn-agentes${nomes.length ? '' : ' vazio'}" title="${esc(nomes.join(', ') || 'Nenhum agente liberado')}"
    aria-label="Agentes de ${esc(u.username)}: ${esc(texto)}"${total ? '' : ' disabled'}>${esc(texto)}</button>`;
}

/* lista de caixinhas com todos os agentes, marcando os ids informados */
function desenharChecksAgentes(box, marcados) {
  box.innerHTML = '';
  if (!estado.adminAgentes.length) {
    box.innerHTML = '<p class="muted">Nenhum agente cadastrado. Cadastre na aba Agentes.</p>';
    return;
  }
  for (const a of estado.adminAgentes) {
    const l = document.createElement('label');
    l.className = `check-agente${a.ativo ? '' : ' inativo'}`;
    l.innerHTML = `<input type="checkbox" value="${a.id}"${marcados.includes(a.id) ? ' checked' : ''}>${iconeAgente(a)}
      <span class="check-texto"><strong>${esc(a.nome)}${a.ativo ? '' : ' <span class="selo selo-off">INATIVO</span>'}</strong>
      ${a.descricao ? `<small>${esc(a.descricao)}</small>` : ''}</span>`;
    box.appendChild(l);
  }
}

const idsMarcados = (box) => $$('input[type=checkbox]:checked', box).map((i) => Number(i.value));

function abrirAgentesUsuario(u, modo = 'agentes') {
  const dlg = $('#dlg-agentes-usuario');
  const aprova = modo === 'aprova';
  $('#agentes-usuario-titulo').textContent = aprova ? `${u.nome || u.username} aprova conhecimento de` : `Agentes de ${u.nome || u.username}`;
  $('#agentes-usuario-dica').textContent = aprova
    ? 'Marque os agentes em que esta pessoa analisa e aprova os arquivos enviados pelos usuários. Aprovadores também veem os conhecimentos aprovados desses agentes; só administradores excluem.'
    : u.perfil === 'admin'
      ? 'Administradores recebem todos os agentes, inclusive os cadastrados depois. Desmarque os que esta pessoa não quer usar no chat.'
      : 'Marque os agentes que esta pessoa pode usar. Tirar a marca corta o acesso na hora.';
  const box = $('#agentes-usuario-lista');
  desenharChecksAgentes(box, aprova ? (u.aprova || []) : u.agentes);
  dlg.returnValue = '';
  dlg.addEventListener('close', async () => {
    if (dlg.returnValue !== 'ok') return;
    const agentes = idsMarcados(box);
    try {
      if (aprova) {
        const r = await enviarJSON(`/api/admin/usuarios/${encodeURIComponent(u.username)}/aprovacoes`, 'PUT', { agentes });
        u.aprova = r.aprova;
        desenharUsuarios();
        toast('Aprovações atualizadas.');
        if (u.username === estado.usuario.username) carregarBaseConhecimento();
        return;
      }
      const r = await enviarJSON(`/api/admin/usuarios/${encodeURIComponent(u.username)}/agentes`, 'PUT', { agentes });
      u.agentes = r.agentes;
      desenharUsuarios();
      toast('Agentes atualizados.');
      if (u.username === estado.usuario.username) { carregarAgentes(); carregarBaseConhecimento(); }
    } catch (e) {
      toast(e.message, 4500);
    }
  }, { once: true });
  dlg.showModal();
}

/* ---- aba Agentes ---- */
async function carregarAgentesAdmin() {
  carregarConfigConhecimento();
  const lista = $('#lista-agentes');
  if (!estado.adminAgentes.length) lista.innerHTML = '<p class="muted">Carregando…</p>';
  try {
    estado.adminAgentes = (await getJSON('/api/admin/agentes')).itens;
  } catch (e) {
    lista.innerHTML = '';
    lista.appendChild(erroBox(e.message));
    return;
  }
  desenharAgentesAdmin();
}

function desenharAgentesAdmin() {
  const lista = $('#lista-agentes');
  lista.innerHTML = '';
  if (!estado.adminAgentes.length) {
    lista.innerHTML = '<p class="muted">Nenhum agente cadastrado. Clique em <b>Adicionar agente</b> e cole a chave de API de um app de conversa do Dify.</p>';
    return;
  }
  for (const a of estado.adminAgentes) {
    const linha = document.createElement('div');
    linha.className = `agente-linha${a.ativo ? '' : ' inativo'}`;
    const qtd = Number(a.qtd_usuarios || 0);
    linha.innerHTML = `
      ${iconeAgente(a, 'agente-icone grande')}
      <div class="info">
        <div class="nome">${esc(a.nome)}${MODO_AGENTE[a.modo] ? `<span class="selo selo-modo">${esc(MODO_AGENTE[a.modo])}</span>` : ''}</div>
        <div class="sub">${esc(a.descricao || 'Sem descrição')}</div>
        <div class="sub mono">${esc(a.chave_mascarada)}</div>
        ${a.recebe_conhecimento ? `<div class="sub conh-selo">📥 Recebe conhecimento · ${a.aprovadores?.length ? `aprovadores: ${esc(a.aprovadores.join(', '))}` : '<b>sem aprovador</b>'}${a.autoaprovacao ? '' : ' · sem autoaprovação'}</div>` : ''}
      </div>
      <div class="qtd">${qtd} ${qtd === 1 ? 'usuário' : 'usuários'}</div>
      <label class="chave"><input type="checkbox"${a.ativo ? ' checked' : ''}><span>${a.ativo ? 'Ativo' : 'Inativo'}</span></label>
      <button class="btn-icone ed" aria-label="Editar ${esc(a.nome)}" title="Editar">${ICONE.editar}</button>
      <button class="btn-icone rem" aria-label="Remover ${esc(a.nome)}" title="Remover agente">${ICONE.lixo}</button>`;

    $('input[type=checkbox]', linha).addEventListener('change', async (ev) => {
      const ativo = ev.target.checked;
      if (!ativo) {
        const ok = await confirmar({ titulo: 'Desativar agente?', texto: `"${a.nome}" some do chat de todos. As liberações são mantidas para quando você reativar.`, ok: 'Desativar' });
        if (ok === null) { ev.target.checked = true; return; }
      }
      try {
        await enviarJSON(`/api/admin/agentes/${a.id}`, 'PATCH', { ativo });
        toast(ativo ? 'Agente ativado.' : 'Agente desativado.');
        carregarAgentesAdmin();
        carregarAgentes();
      } catch (e) {
        ev.target.checked = !ativo;
        toast(e.message, 4500);
      }
    });
    $('.ed', linha).addEventListener('click', () => abrirDialogoAgente(a));
    $('.rem', linha).addEventListener('click', async () => {
      const ok = await confirmar({
        titulo: 'Remover agente?',
        texto: `"${a.nome}" sai da plataforma e ${qtd} ${qtd === 1 ? 'pessoa perde' : 'pessoas perdem'} o acesso. As conversas continuam guardadas no Dify; se cadastrar a mesma chave de novo, elas voltam a aparecer.`,
        ok: 'Remover',
      });
      if (ok === null) return;
      try {
        await api(`/api/admin/agentes/${a.id}`, { metodo: 'DELETE' });
        toast('Agente removido.');
        carregarAgentesAdmin();
        carregarAgentes();
      } catch (e) {
        toast(e.message, 4500);
      }
    });
    lista.appendChild(linha);
  }
}

/* sugestões: uma por linha, "Categoria | pergunta" */
function sugestoesParaTexto(lista) {
  return (lista || []).map((s) => (s.cat ? `${s.cat} | ${s.txt}` : s.txt)).join('\n');
}

function textoParaSugestoes(texto) {
  return texto.split('\n').map((l) => l.trim()).filter(Boolean).slice(0, 12).map((l) => {
    const i = l.indexOf('|');
    if (i < 0) return { cat: '', txt: l.slice(0, 300) };
    return { cat: l.slice(0, i).trim().slice(0, 40), txt: l.slice(i + 1).trim().slice(0, 300) };
  }).filter((s) => s.txt);
}

let agenteEditando = null;

function abrirDialogoAgente(a = null) {
  agenteEditando = a;
  const editar = !!a;
  $('#agente-titulo').textContent = editar ? `Editar ${a.nome}` : 'Adicionar agente';
  $('#agente-dica-chave').hidden = editar;
  $('#agente-chave-rotulo').textContent = editar ? `Trocar a chave da API (atual: ${a.chave_mascarada})` : 'Chave da API do Dify';
  $('#agente-chave').value = '';
  $('#agente-chave').placeholder = editar ? 'Deixe em branco para manter a chave atual' : 'app-…';
  $('#agente-nome').value = editar ? a.nome : '';
  $('#agente-nome').placeholder = editar ? '' : 'Em branco: usa o nome do app no Dify';
  $('#agente-campos-edicao').hidden = !editar;
  $('#agente-descricao').value = editar ? a.descricao || '' : '';
  $('#agente-sugestoes').value = editar ? sugestoesParaTexto(a.sugestoes) : '';
  $('#agente-recebe').checked = !!a?.recebe_conhecimento;
  carregarBasesNoDialogo(a);
  $('#agente-autoaprovacao').checked = a ? !!a.autoaprovacao : true;
  $('#agente-conh-dica').innerHTML = 'Escolha a base do Dify onde entra o que for aprovado (por exemplo, <b>Feedback</b>). '
    + 'Depois, no Dify, adicione essa base ao agente (Agente &gt; Conhecimento &gt; Adicionar &gt; Publicar). '
    + 'Enquanto esperam a aprovação, os envios ficam só nesta plataforma: nada chega ao Dify antes de aprovado.';
  $('#agente-erro').hidden = true;
  $('#agente-salvar').disabled = false;
  $('#agente-salvar').textContent = editar ? 'Salvar' : 'Verificar e cadastrar';
  $('#dlg-agente').showModal();
  setTimeout(() => (editar ? $('#agente-nome') : $('#agente-chave')).focus(), 30);
}

async function salvarAgente(e) {
  e.preventDefault();
  if (e.submitter?.value === 'cancel') { $('#dlg-agente').close(); return; }
  const erro = $('#agente-erro');
  const btn = $('#agente-salvar');
  const a = agenteEditando;
  const chave = $('#agente-chave').value.trim();
  const nome = $('#agente-nome').value.trim();
  erro.hidden = true;
  let corpo;
  if (a) {
    if (!nome) { erro.textContent = 'Informe o nome do agente.'; erro.hidden = false; return; }
    corpo = { nome, descricao: $('#agente-descricao').value.trim(), sugestoes: textoParaSugestoes($('#agente-sugestoes').value),
      recebe_conhecimento: $('#agente-recebe').checked, autoaprovacao: $('#agente-autoaprovacao').checked };
    if ($('#agente-base').value && $('#agente-base').value !== (a.dataset_id || '')) corpo.dataset_id = $('#agente-base').value;
    if ($('#agente-base-feedback').value !== (a.dataset_feedback_id || '')) corpo.dataset_feedback_id = $('#agente-base-feedback').value;
    if (chave) corpo.chave = chave;
  } else {
    if (!chave) { erro.textContent = 'Cole a chave da API do Dify (começa com app-).'; erro.hidden = false; return; }
    corpo = { chave, nome };
  }
  btn.disabled = true;
  const rotulo = btn.textContent;
  btn.textContent = chave ? 'Conferindo no Dify…' : 'Salvando…';
  try {
    const r = a
      ? await enviarJSON(`/api/admin/agentes/${a.id}`, 'PATCH', corpo)
      : await enviarJSON('/api/admin/agentes', 'POST', corpo);
    $('#dlg-agente').close();
    const ligouAgora = a && r.recebe_conhecimento && r.dataset_id && r.dataset_id !== a.dataset_id;
    const nomeBase = basesDify.find((b) => b.id === r.dataset_id)?.nome || `Colaborativo - ${r.nome}`;
    toast(ligouAgora ? `Base "${nomeBase}" ligada a este agente. No Dify, adicione essa base ao agente (Conhecimento > Adicionar > Publicar) e marque os aprovadores na aba Usuários.`
      : a ? 'Agente atualizado.' : `"${r.nome}" cadastrado. Administradores já têm acesso; libere para os demais na aba Usuários.`, ligouAgora ? 12000 : 6000);
    if (a) carregarBaseConhecimento();
    carregarAgentesAdmin();
    carregarAgentes();
  } catch (err) {
    erro.textContent = err.message;
    erro.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = rotulo;
  }
}

/* ---- filtros comuns (Conversas e Registros) ---- */
const hojeISO = (dias = 0) => {
  const d = new Date();
  d.setDate(d.getDate() + dias);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

/* usuários e agentes para os filtros (vêm das outras abas; carrega se ainda não veio) */
async function prepararFiltrosAdmin() {
  if (!estado.usuarios.length || !estado.adminAgentes.length) {
    try {
      const [usuarios, agentes] = await Promise.all([getJSON('/api/admin/usuarios'), getJSON('/api/admin/agentes')]);
      estado.usuarios = usuarios.itens;
      estado.adminAgentes = agentes.itens;
    } catch (e) {
      toast(e.message);
    }
  }
  $('#lista-logins').innerHTML = estado.usuarios
    .map((u) => `<option value="${esc(u.username)}">${esc(u.nome || u.username)}</option>`).join('');
  for (const id of ['#conv-agente', '#reg-agente']) {
    const sel = $(id);
    const atual = sel.value;
    sel.innerHTML = '<option value="">Todos os agentes</option>' + estado.adminAgentes
      .map((a) => `<option value="${a.id}">${esc(a.nome)}${a.ativo ? '' : ' (inativo)'}</option>`).join('');
    sel.value = atual;
  }
}

function lerFiltros(prefixo) {
  const f = {};
  const usuario = $(`#${prefixo}-usuario`).value.trim();
  const agente = $(`#${prefixo}-agente`).value;
  const inicio = $(`#${prefixo}-inicio`).value;
  const fim = $(`#${prefixo}-fim`).value;
  if (usuario) f.usuario = usuario;
  if (agente) f.agente = Number(agente);
  if (inicio) f.inicio = inicio;
  if (fim) f.fim = fim;
  return f;
}

const paraQuery = (f) => new URLSearchParams(Object.entries(f).map(([k, v]) => [k, String(v)])).toString();

/* ---- aba Registros ---- */
let registrosIniciados = false;

async function abrirRegistros() {
  await prepararFiltrosAdmin();
  if (!registrosIniciados) {
    registrosIniciados = true;
    $('#reg-inicio').value = hojeISO(-7);
  }
  carregarRegistros();
}

async function carregarRegistros() {
  const lista = $('#lista-acessos');
  const resumo = $('#reg-resumo');
  const f = { ...lerFiltros('reg'), tipo: $('#reg-tipo').value };
  lista.innerHTML = '<p class="muted">Carregando…</p>';
  resumo.textContent = '';
  try {
    const { itens, cortado } = await getJSON(`/api/admin/registros?${paraQuery(f)}`);
    lista.innerHTML = itens.length ? '' : '<p class="muted">Nenhum registro com esses filtros.</p>';
    resumo.textContent = itens.length
      ? `${itens.length} registro${itens.length === 1 ? '' : 's'}${cortado ? ' (os mais recentes; refine os filtros para ver os anteriores)' : ''}`
        + (f.agente ? ' · com agente escolhido, os logins não aparecem (login não pertence a um agente)' : '')
      : '';
    const frag = document.createDocumentFragment();
    for (const r of itens) {
      const uso = r.tipo === 'uso';
      const [rotulo, classe] = (uso ? EVENTO_USO[r.evento] : RESULTADO_ACESSO[r.evento]) || [r.evento, 'falha'];
      const d = document.createElement('div');
      d.className = `acesso${uso ? ' uso' : ''}`;
      const detalhe = uso ? r.detalhe || '' : [r.ip, r.detalhe].filter(Boolean).join(' · ');
      d.innerHTML = `<span>${esc(dataHora(r.quando))}</span><span class="reg-usuario">${esc(r.username || '—')}</span>`
        + `<span class="${classe}">${esc(rotulo)}</span><span class="reg-agente">${esc(r.agente_nome || (uso ? '—' : ''))}</span>`
        + `<span class="muted reg-detalhe" title="${esc(detalhe)}">${esc(detalhe)}</span>`;
      if (uso && r.conversation_id && r.agente_id) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn-texto reg-ver';
        b.textContent = 'Ver conversa';
        b.addEventListener('click', () => abrirConversaAdmin({
          agente_id: r.agente_id, agente: r.agente_nome, username: r.username, usuario: nomeDoUsuario(r.username),
          id: r.conversation_id, titulo: 'Conversa',
        }));
        d.appendChild(b);
      }
      frag.appendChild(d);
    }
    lista.appendChild(frag);
  } catch (e) {
    lista.innerHTML = '';
    lista.appendChild(erroBox(e.message));
  }
}

const nomeDoUsuario = (login) => estado.usuarios.find((u) => u.username === login)?.nome || login;

/* ---- aba Conversas ---- */
const LIMITE_LISTA_CONVERSAS = 500;  // a tela mostra até 500; exportar todas leva o filtro inteiro
let conversasIniciadas = false;
let convAdmin = { itens: [], filtro: {}, marcadas: new Set() };
const chaveConv = (c) => `${c.agente_id}|${c.username}|${c.id}`;

async function abrirConversasAdmin() {
  await prepararFiltrosAdmin();
  if (!conversasIniciadas) {
    conversasIniciadas = true;
    $('#conv-inicio').value = hojeISO(-30);
    buscarConversasAdmin();
  }
}

async function buscarConversasAdmin() {
  const lista = $('#lista-conv-admin');
  const filtro = lerFiltros('conv');
  $('#barra-conversas').hidden = true;
  $('#conv-avisos').innerHTML = '';
  lista.innerHTML = '<p class="muted"><span class="spinner" style="display:inline-block;vertical-align:-2px"></span> Buscando as conversas no Dify…</p>';
  try {
    const { itens, avisos } = await getJSON(`/api/admin/conversas?${paraQuery(filtro)}`);
    convAdmin = { itens, filtro, marcadas: new Set() };
    for (const a of avisos) $('#conv-avisos').appendChild(erroBox(a));
    desenharConversasAdmin();
  } catch (e) {
    lista.innerHTML = '';
    lista.appendChild(erroBox(e.message));
  }
}

function desenharConversasAdmin() {
  const lista = $('#lista-conv-admin');
  const { itens } = convAdmin;
  lista.innerHTML = '';
  $('#barra-conversas').hidden = !itens.length;
  if (!itens.length) {
    lista.innerHTML = '<p class="muted">Nenhuma conversa com esses filtros.</p>';
    return;
  }
  const frag = document.createDocumentFragment();
  for (const c of itens.slice(0, LIMITE_LISTA_CONVERSAS)) {
    const d = document.createElement('div');
    d.className = 'conv-linha';
    d.innerHTML = `<input type="checkbox" aria-label="Selecionar ${esc(c.titulo)}"${convAdmin.marcadas.has(chaveConv(c)) ? ' checked' : ''}>
      <div class="info"><div class="nome" title="${esc(c.titulo)}">${esc(c.titulo)}</div>
        <div class="sub">${esc(c.usuario)}${c.usuario !== c.username ? ` (${esc(c.username)})` : ''} · ${esc(c.agente)}</div></div>
      <div class="sub quando">${esc(dataHora(c.atualizado))}</div>
      <button type="button" class="btn btn-texto">Ver</button>`;
    $('input', d).addEventListener('change', (ev) => {
      if (ev.target.checked) convAdmin.marcadas.add(chaveConv(c));
      else convAdmin.marcadas.delete(chaveConv(c));
      atualizarBarraConversas();
    });
    $('button', d).addEventListener('click', () => abrirConversaAdmin(c));
    frag.appendChild(d);
  }
  lista.appendChild(frag);
  if (itens.length > LIMITE_LISTA_CONVERSAS) {
    lista.insertAdjacentHTML('beforeend', `<p class="muted">Mostrando as ${LIMITE_LISTA_CONVERSAS} mais recentes de ${itens.length}. `
      + '"Exportar todas" leva todas as do filtro.</p>');
  }
  atualizarBarraConversas();
}

function atualizarBarraConversas() {
  const total = convAdmin.itens.length;
  const n = convAdmin.marcadas.size;
  const visiveis = Math.min(total, LIMITE_LISTA_CONVERSAS);
  $('#conv-resumo').textContent = `${total} conversa${total === 1 ? '' : 's'}${n ? ` · ${n} selecionada${n === 1 ? '' : 's'}` : ''}`;
  $('#conv-todas').checked = n > 0 && n === visiveis;
  $('#conv-todas').indeterminate = n > 0 && n < visiveis;
  $('#btn-exportar-sel').disabled = !n;
  $('#btn-exportar-sel').textContent = n ? `Exportar selecionadas (${n})` : 'Exportar selecionadas';
}

function marcarTodasConversas(marcar) {
  convAdmin.marcadas = new Set(marcar ? convAdmin.itens.slice(0, LIMITE_LISTA_CONVERSAS).map(chaveConv) : []);
  for (const i of $$('.conv-linha input', $('#lista-conv-admin'))) i.checked = marcar;
  atualizarBarraConversas();
}

const refConversa = (c) => ({ agente_id: c.agente_id, username: c.username, conversation_id: c.id,
  titulo: c.titulo || '', criado: c.criado ?? null, atualizado: c.atualizado ?? null });

/* baixa o TXT gerado pelo servidor */
async function exportarConversas(corpo, botao) {
  const rotulo = botao?.textContent;
  if (botao) { botao.disabled = true; botao.textContent = 'Gerando o arquivo…'; }
  try {
    const r = await api('/api/admin/conversas/exportar', { metodo: 'POST', corpo });
    const nome = /filename="([^"]+)"/.exec(r.headers.get('content-disposition') || '')?.[1] || 'conversas.txt';
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = nome;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast(`Arquivo ${nome} baixado.`);
  } catch (e) {
    toast(e.message, 6000);
  } finally {
    if (botao) { botao.disabled = false; botao.textContent = rotulo; }
  }
}

async function exportarSelecionadas() {
  const escolhidas = convAdmin.itens.filter((c) => convAdmin.marcadas.has(chaveConv(c)));
  if (escolhidas.length) await exportarConversas({ itens: escolhidas.map(refConversa) }, $('#btn-exportar-sel'));
  atualizarBarraConversas();
}

async function exportarTodas() {
  const total = convAdmin.itens.length;
  if (total > 50) {
    const ok = await confirmar({ titulo: `Exportar ${total} conversas?`, texto: 'O arquivo pode demorar um pouco para ser gerado.', ok: 'Exportar' });
    if (ok === null) return;
  }
  await exportarConversas({ filtro: convAdmin.filtro }, $('#btn-exportar-tudo'));
}

/* ---- leitor de conversa (somente leitura) ---- */
let conversaAberta = null;

async function abrirConversaAdmin(c) {
  conversaAberta = c;
  const dlg = $('#dlg-conversa');
  $('#conv-titulo').textContent = c.titulo || 'Conversa';
  $('#conv-sub').textContent = `${c.usuario || c.username}${c.usuario && c.usuario !== c.username ? ` (${c.username})` : ''} · ${c.agente || ''}`
    + (c.criado ? ` · ${dataHora(c.criado)} a ${dataHora(c.atualizado)}` : '');
  const box = $('#conv-mensagens');
  box.innerHTML = '<p class="muted"><span class="spinner" style="display:inline-block;vertical-align:-2px"></span> Carregando a conversa…</p>';
  if (!dlg.open) dlg.showModal();
  try {
    const { mensagens } = await getJSON(`/api/admin/conversas/${c.agente_id}/${encodeURIComponent(c.username)}/${encodeURIComponent(c.id)}`);
    if (conversaAberta !== c) return;
    box.innerHTML = mensagens.length ? '' : '<p class="muted">Conversa sem mensagens.</p>';
    for (const m of mensagens) {
      const u = bolhaUsuario(m.pergunta, m.anexos || []);
      u.insertAdjacentHTML('afterbegin', `<span class="conv-hora">${esc(dataHora(m.criado))}</span>`);
      box.appendChild(u);
      const bot = bolhaBot();
      renderMarkdown(bot.md, m.resposta || '_(sem resposta)_');
      if (m.fontes?.length) {
        bot.conteudo.insertAdjacentHTML('beforeend', `<details class="fontes"><summary>Fontes consultadas (${m.fontes.length})</summary><ul>${m.fontes
          .map((f) => `<li>${esc(f.documento)}${f.base ? ` <span class="muted">· ${esc(f.base)}</span>` : ''}</li>`).join('')}</ul></details>`);
      }
      if (m.avaliacao) {
        bot.conteudo.insertAdjacentHTML('beforeend', `<p class="conv-avaliacao ${m.avaliacao}">${m.avaliacao === 'like' ? '👍 Avaliada como útil' : '👎 Avaliada como ruim'}</p>`);
      }
      box.appendChild(bot.raiz);
    }
  } catch (e) {
    if (conversaAberta !== c) return;
    box.innerHTML = '';
    box.appendChild(erroBox(e.status === 404 ? 'Conversa não encontrada no Dify (pode ter sido apagada).' : e.message));
  }
}

/* ------------------------------------------------------------------ conhecimento (envio com aprovação) */
const STATUS_ENVIO = {
  processando: ['Processando…', 'neutro'],
  pendente: ['Aguardando aprovação', 'aviso'],
  publicando: ['Publicando…', 'neutro'],
  erro: ['Erro no processamento', 'falha'],
  aprovado: ['Aprovado', 'ok'],
  rejeitado: ['Rejeitado', 'falha'],
  cancelado: ['Cancelado', 'neutro'],
  desativado: ['Desativado', 'aviso'],
  excluido: ['Excluído', 'falha'],
};
const ACAO_CONH = {
  enviado: 'Enviado', processado: 'Pronto para análise', erro: 'Erro no processamento', cancelado: 'Cancelado por quem enviou',
  aprovado: 'Aprovado', autoaprovado: 'Autoaprovado', rejeitado: 'Rejeitado', desativado: 'Desativado (admin)',
  reativado: 'Reativado (admin)', excluido: 'Excluído (admin)', retomado: 'Retomado após reinício',
};
const NIVEL_ALERTA = { alto: 'Atenção', medio: 'Verifique', info: 'Informação' };

let conh = { agentes: [], aprova: [], admin: false, max_mb: 15, extensoes: [] };
let abaConh = 'enviar';
let timerMeusEnvios = null;

const tamanhoLegivel = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1).replace('.', ',')} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
const seloStatus = (st) => { const [t, c] = STATUS_ENVIO[st] || [st, 'neutro']; return `<span class="selo-status ${c}">${esc(t)}</span>`; };
const podeAprovar = () => conh.admin || conh.aprova.length > 0;

/* quem pode enviar (e para quais agentes) e quem aprova: define os itens do menu */
async function carregarBaseConhecimento() {
  try {
    const r = await getJSON('/api/conhecimento/agentes');
    conh = { ...r, agentes: r.itens };
  } catch {
    conh = { agentes: [], aprova: [], admin: false, max_mb: 15, extensoes: [] };
  }
  $('#menu-enviar').hidden = !conh.agentes.length;
  $('#menu-aprovacoes').hidden = !podeAprovar();
  atualizarContadorPendentes();
}

async function atualizarContadorPendentes() {
  if (!podeAprovar()) return;
  try {
    const { itens } = await getJSON('/api/conhecimento/envios?status=pendente');
    const n = itens.filter((e) => e.pode_decidir).length;
    for (const id of ['#contador-pendentes', '#contador-aba']) {
      $(id).textContent = String(n);
      $(id).hidden = !n;
    }
  } catch { /* sem permissão ou sem rede: só não mostra o número */ }
}

function abrirConhecimento(aba = 'enviar') {
  fecharMenu();
  fecharLateral();
  $('#view-chat').hidden = true;
  $('#view-admin').hidden = true;
  $('#lateral').hidden = true;
  $('#view-conhecimento').hidden = false;
  history.replaceState(null, '', '/conhecimento');
  for (const b of $$('.aba-conh')) {
    if (b.dataset.aba !== 'enviar') b.hidden = !podeAprovar();
  }
  $('.aba-conh[data-aba="enviar"]').hidden = !conh.agentes.length;
  if (aba === 'enviar' && !conh.agentes.length) aba = 'pendentes';
  trocarAbaConh(aba);
}

function trocarAbaConh(aba) {
  abaConh = aba;
  for (const b of $$('.aba-conh')) {
    b.classList.toggle('ativa', b.dataset.aba === aba);
    b.setAttribute('aria-selected', String(b.dataset.aba === aba));
  }
  for (const nome of ['enviar', 'pendentes', 'aprovados', 'registros']) $(`#conh-${nome}`).hidden = nome !== aba;
  clearTimeout(timerMeusEnvios);
  if (aba === 'enviar') prepararFormEnvio();
  else if (aba === 'pendentes') carregarPendentes();
  else if (aba === 'aprovados') abrirAprovados();
  else abrirRegistrosConh();
}

/* ---- enviar ---- */
function prepararFormEnvio() {
  const sel = $('#envio-agente');
  const atual = sel.value;
  sel.innerHTML = conh.agentes.map((a) => `<option value="${a.id}">${esc(a.nome)}</option>`).join('');
  if (atual) sel.value = atual;
  else if (estado.agenteAtual && conh.agentes.some((a) => a.id === estado.agenteAtual.id)) sel.value = String(estado.agenteAtual.id);
  $('#envio-arquivo').accept = conh.extensoes.map((e) => `.${e}`).join(',');
  const ocr = conh.extensoes.filter((e) => ['pdf', 'ppt', 'pptx', 'doc', 'docx', 'jpg', 'png'].includes(e));
  $('#envio-dica').textContent = `Aceita ${conh.extensoes.map((e) => e.toUpperCase()).join(', ')} até ${conh.max_mb} MB. `
    + (ocr.length ? 'PDFs escaneados, apresentações, imagens e textos dentro de figuras passam por leitura automática (OCR). ' : '')
    + 'O arquivo fica em quarentena até um aprovador analisar.';
  carregarMeusEnvios();
}

async function enviarConhecimento(e) {
  e.preventDefault();
  const erro = $('#envio-erro');
  const arq = $('#envio-arquivo').files[0];
  const justificativa = $('#envio-justificativa').value.trim();
  erro.hidden = true;
  const falha = (m) => { erro.textContent = m; erro.hidden = false; };
  if (!arq) return falha('Escolha o arquivo.');
  const ext = (arq.name.split('.').pop() || '').toLowerCase();
  if (!conh.extensoes.includes(ext)) return falha(`Tipo de arquivo não aceito (.${ext}).`);
  if (arq.size > conh.max_mb * 1048576) return falha(`O arquivo tem ${tamanhoLegivel(arq.size)}; o limite é ${conh.max_mb} MB.`);
  if (justificativa.length < 10) return falha('Explique em poucas palavras o que é o arquivo e por que deve entrar.');
  const btn = $('#envio-botao');
  btn.disabled = true;
  btn.textContent = 'Enviando…';
  try {
    await api(`/api/conhecimento/envios?agente=${encodeURIComponent($('#envio-agente').value)}`, {
      metodo: 'POST', bruto: new Blob([arq], { type: 'application/octet-stream' }),
      cabecalhos: { 'X-Arquivo-Nome': encodeURIComponent(arq.name), 'X-Justificativa': encodeURIComponent(justificativa) },
    });
    $('#envio-arquivo').value = '';
    $('#envio-justificativa').value = '';
    toast('Arquivo enviado. Ele vai para análise assim que terminar de ser processado.', 5000);
    carregarMeusEnvios();
  } catch (err) {
    falha(err.status === 413 && err.message.startsWith('Erro 413') ? 'O servidor recusou o arquivo por tamanho.' : err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Enviar para aprovação';
  }
}

/* o que os trechos mostram: no 👎 pendente é a cópia da quarentena (com a resposta errada), não o que será publicado */
function rotuloTrechos(e, deFeedback) {
  if (!deFeedback) return 'Trechos que o agente vai consultar';
  if (['aprovado', 'desativado'].includes(e.status)) return 'Texto publicado na base do agente';
  if (e.origem === 'feedback_negativo') return 'Cópia em quarentena, só para conferência (a resposta errada NÃO vai para a base; vai só a resposta correta acima)';
  return 'Texto que vai para a base do agente';
}

const TITULO_ORIGEM = { feedback_positivo: '👍 Resposta validada', feedback_negativo: '✏️ Correção' };
const tituloEnvio = (e) => (TITULO_ORIGEM[e.origem] ? `${TITULO_ORIGEM[e.origem]}: ${(e.pergunta || '').slice(0, 90)}` : e.arquivo_nome);

function linhaEnvio(e, { mostrarQuem = false } = {}) {
  const d = document.createElement('div');
  d.className = 'envio-linha-lista';
  const alertasAltos = (e.alertas || []).filter((a) => a.nivel === 'alto').length;
  d.innerHTML = `
    <div class="info">
      <div class="nome" title="${esc(tituloEnvio(e))}">${esc(tituloEnvio(e))}</div>
      <div class="sub">${mostrarQuem ? `${esc(nomeDoUsuario(e.username))} · ` : ''}${esc(e.agente_nome || '')} · ${esc(dataHora(e.criado_em))} · ${esc(tamanhoLegivel(e.tamanho))}${e.autoaprovado ? ' · <b>autoaprovado</b>' : ''}</div>
      ${e.status === 'rejeitado' && e.motivo ? `<div class="sub motivo">Motivo: ${esc(e.motivo)}</div>` : ''}
      ${e.status === 'erro' && e.erro ? `<div class="sub motivo">${esc(e.erro)}</div>` : ''}
    </div>
    <div class="estado">${seloStatus(e.status)}${alertasAltos ? `<span class="selo-status falha" title="Alertas importantes">⚠ ${alertasAltos}</span>` : ''}</div>
    <button type="button" class="btn btn-texto">Ver</button>`;
  $('button', d).addEventListener('click', () => abrirEnvio(e.id));
  return d;
}

async function carregarMeusEnvios() {
  const lista = $('#lista-meus-envios');
  try {
    const { itens } = await getJSON('/api/conhecimento/meus');
    lista.innerHTML = itens.length ? '' : '<p class="muted">Você ainda não enviou nenhum arquivo.</p>';
    for (const e of itens) lista.appendChild(linhaEnvio(e));
    clearTimeout(timerMeusEnvios);  // enquanto houver arquivo em processamento, atualiza sozinho
    if (abaConh === 'enviar' && !$('#view-conhecimento').hidden && itens.some((e) => ['processando', 'publicando'].includes(e.status))) {
      timerMeusEnvios = setTimeout(carregarMeusEnvios, 3000);
    }
  } catch (err) {
    lista.innerHTML = '';
    lista.appendChild(erroBox(err.message));
  }
}

/* ---- pendentes ---- */
async function carregarPendentes() {
  const lista = $('#lista-pendentes');
  lista.innerHTML = '<p class="muted">Carregando…</p>';
  try {
    const { itens } = await getJSON('/api/conhecimento/envios?status=pendente,processando');
    lista.innerHTML = itens.length ? '' : '<p class="muted">Nada aguardando aprovação. 🎉</p>';
    for (const e of itens) lista.appendChild(linhaEnvio(e, { mostrarQuem: true }));
    atualizarContadorPendentes();
  } catch (err) {
    lista.innerHTML = '';
    lista.appendChild(erroBox(err.message));
  }
}

/* ---- aprovados ---- */
let aprovadosIniciados = false;

function opcoesAgentesConh(sel) {
  const ids = conh.admin ? null : conh.aprova;
  const lista = (estado.adminAgentes.length ? estado.adminAgentes : estado.agentes).filter((a) => !ids || ids.includes(a.id));
  const atual = sel.value;
  sel.innerHTML = '<option value="">Todos que vejo</option>' + lista.map((a) => `<option value="${a.id}">${esc(a.nome)}</option>`).join('');
  sel.value = atual;
}

async function abrirAprovados() {
  if (conh.admin) await prepararFiltrosAdmin();
  opcoesAgentesConh($('#apr-agente'));
  aprovadosIniciados = true;
  carregarAprovados();
}

async function carregarAprovados() {
  const lista = $('#lista-aprovados');
  const f = { status: $('#apr-status').value };
  if ($('#apr-agente').value) f.agente = $('#apr-agente').value;
  if ($('#apr-origem').value) f.origem = $('#apr-origem').value;
  if ($('#apr-usuario').value.trim()) f.usuario = $('#apr-usuario').value.trim();
  if ($('#apr-inicio').value) f.inicio = $('#apr-inicio').value;
  if ($('#apr-fim').value) f.fim = $('#apr-fim').value;
  lista.innerHTML = '<p class="muted">Carregando…</p>';
  try {
    const { itens } = await getJSON(`/api/conhecimento/envios?${paraQuery(f)}`);
    lista.innerHTML = itens.length ? '' : '<p class="muted">Nenhum conhecimento com esses filtros.</p>';
    for (const e of itens) lista.appendChild(linhaEnvio(e, { mostrarQuem: true }));
  } catch (err) {
    lista.innerHTML = '';
    lista.appendChild(erroBox(err.message));
  }
}

/* ---- registros de aprovação ---- */
let regConhIniciado = false;

async function abrirRegistrosConh() {
  if (conh.admin) await prepararFiltrosAdmin();
  opcoesAgentesConh($('#rc-agente'));
  if (!regConhIniciado) {
    regConhIniciado = true;
    $('#rc-inicio').value = hojeISO(-30);
  }
  carregarRegistrosConh();
}

function filtroRegConh() {
  const f = {};
  if ($('#rc-agente').value) f.agente = $('#rc-agente').value;
  if ($('#rc-usuario').value.trim()) f.usuario = $('#rc-usuario').value.trim();
  if ($('#rc-acao').value) f.acao = $('#rc-acao').value;
  if ($('#rc-inicio').value) f.inicio = $('#rc-inicio').value;
  if ($('#rc-fim').value) f.fim = $('#rc-fim').value;
  return f;
}

async function carregarRegistrosConh() {
  const lista = $('#lista-reg-conh');
  lista.innerHTML = '<p class="muted">Carregando…</p>';
  try {
    const { itens, cortado } = await getJSON(`/api/conhecimento/registros?${paraQuery(filtroRegConh())}`);
    $('#rc-resumo').textContent = itens.length ? `${itens.length} registro${itens.length === 1 ? '' : 's'}${cortado ? ' (os mais recentes)' : ''}` : '';
    lista.innerHTML = itens.length ? '' : '<p class="muted">Nenhum registro com esses filtros.</p>';
    for (const r of itens) {
      const d = document.createElement('div');
      d.className = 'acesso reg-conh';
      const falha = ['rejeitado', 'erro', 'excluido', 'desativado'].includes(r.acao);
      d.innerHTML = `<span>${esc(dataHora(r.quando))}</span><span class="reg-usuario">${esc(r.autor)}</span>`
        + `<span class="${falha ? 'falha' : 'ok'}">${esc(ACAO_CONH[r.acao] || r.acao)}</span>`
        + `<span class="reg-agente" title="${esc(r.arquivo_nome)}">${esc(r.arquivo_nome)} · ${esc(r.agente_nome || '')}</span>`
        + `<span class="muted reg-detalhe" title="${esc(r.detalhe || '')}">${esc(r.detalhe || '')}</span>`;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-texto reg-ver';
      b.textContent = 'Ver envio';
      b.addEventListener('click', () => abrirEnvio(r.envio_id));
      d.appendChild(b);
      lista.appendChild(d);
    }
  } catch (err) {
    lista.innerHTML = '';
    lista.appendChild(erroBox(err.message));
  }
}

async function exportarRegistrosConh() {
  try {
    const r = await api(`/api/conhecimento/registros?${paraQuery({ ...filtroRegConh(), formato: 'csv' })}`);
    const nome = /filename="([^"]+)"/.exec(r.headers.get('content-disposition') || '')?.[1] || 'registros-aprovacao.csv';
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = nome;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast(`Arquivo ${nome} baixado.`);
  } catch (err) {
    toast(err.message, 5000);
  }
}

/* ---- detalhe do envio ---- */
let envioAberto = null;

async function abrirEnvio(id) {
  const dlg = $('#dlg-envio');
  envioAberto = id;
  $('#envio-titulo').textContent = 'Carregando…';
  $('#envio-sub').textContent = '';
  $('#envio-detalhe').innerHTML = '<p class="muted"><span class="spinner" style="display:inline-block;vertical-align:-2px"></span> Carregando…</p>';
  $('#envio-acoes').innerHTML = '';
  if (!dlg.open) dlg.showModal();
  let e;
  try {
    e = await getJSON(`/api/conhecimento/envios/${id}`);
  } catch (err) {
    $('#envio-detalhe').innerHTML = '';
    $('#envio-detalhe').appendChild(erroBox(err.message));
    return;
  }
  if (envioAberto !== id) return;
  desenharEnvio(e);
}

function desenharEnvio(e) {
  $('#envio-titulo').textContent = TITULO_ORIGEM[e.origem] || e.arquivo_nome;
  $('#envio-sub').innerHTML = `${seloStatus(e.status)} ${esc(e.agente_nome || '')} · enviado por ${esc(nomeDoUsuario(e.username))} em ${esc(dataHora(e.criado_em))}`
    + (e.decidido_por ? ` · decidido por ${esc(nomeDoUsuario(e.decidido_por))}${e.autoaprovado ? ' <b>(autoaprovado)</b>' : ''}` : '');
  const info = [
    tamanhoLegivel(e.tamanho),
    e.convertido ? `convertido para PDF${e.ocr && e.ocr !== 'nao_aplicavel' ? `, OCR ${e.ocr}` : ''}` : (e.ocr === 'nao_aplicavel' || !e.ocr ? 'enviado como está' : ''),
    e.paginas ? `${e.paginas} página${e.paginas === 1 ? '' : 's'}` : '',
    e.caracteres != null ? `${Number(e.caracteres).toLocaleString('pt-BR')} caracteres extraídos` : '',
    e.trechos != null ? `${e.trechos} trecho${e.trechos === 1 ? '' : 's'}` : '',
  ].filter(Boolean).join(' · ');
  const alertas = (e.alertas || []).map((a) => `
    <div class="alerta ${esc(a.nivel)}"><strong>${esc(NIVEL_ALERTA[a.nivel] || '')}: ${esc(a.titulo)}</strong>
      <p>${esc(a.detalhe)}</p>${(a.exemplos || []).map((x) => `<code>${esc(x)}</code>`).join('')}</div>`).join('');
  const temConteudo = ['pendente', 'publicando', 'aprovado', 'desativado'].includes(e.status);
  const deFeedback = e.origem && e.origem !== 'arquivo';
  const box = $('#envio-detalhe');
  box.innerHTML = `
    ${deFeedback ? `<div class="envio-bloco"><b>Pergunta feita ao agente</b><p class="pre">${esc(e.pergunta || '')}</p></div>
      <div class="envio-bloco"><b>${e.origem === 'feedback_negativo' ? 'Resposta do agente (avaliada como ruim)' : 'Resposta do agente (avaliada como útil)'}</b>
        <div class="md resposta-original"></div></div>
      ${e.origem === 'feedback_negativo' ? (e.pode_decidir
    ? `<div class="envio-bloco correcao"><label for="aprov-correcao"><b>Resposta correta que vai para a base do agente</b></label>
        <p class="muted dica">${e.resposta_corrigida ? `Escrita por ${esc(nomeDoUsuario(e.username))}; você pode ajustar antes de aprovar.` : 'Quem avaliou não escreveu a correção: escreva a resposta certa para aprovar.'}</p>
        <textarea id="aprov-correcao" rows="6" maxlength="8000"></textarea></div>`
    : `<div class="envio-bloco correcao"><b>Resposta correta</b><p class="pre">${esc(e.resposta_corrigida || (e.status === 'pendente' ? 'Ainda não escrita: o aprovador escreve ao aprovar.' : '—'))}</p></div>`) : ''}` : ''}
    <div class="envio-bloco"><b>Justificativa</b><p>${esc(e.justificativa)}</p><p class="muted">${esc(info)}</p>
      ${e.motivo ? `<p><b>${e.status === 'rejeitado' ? 'Motivo da rejeição' : 'Comentário da decisão'}:</b> ${esc(e.motivo)}</p>` : ''}
      ${e.erro ? `<p class="falha-texto">${esc(e.erro)}</p>` : ''}
      ${e.status === 'pendente' && !e.pode_decidir && e.username === estado.usuario.username && e.aprovadores?.includes(e.username) && !e.autoaprovacao_permitida
        ? '<p class="muted">A autoaprovação está desligada neste agente: outro aprovador precisa decidir.</p>' : ''}
      ${e.status === 'pendente' && e.aprovadores && !e.aprovadores.length ? '<p class="falha-texto">Este agente ainda não tem aprovador. Peça ao administrador para marcar um.</p>' : ''}
    </div>
    ${alertas ? `<div class="envio-bloco"><b>Alertas automáticos</b>${alertas}</div>` : (temConteudo ? '<div class="envio-bloco"><b>Alertas automáticos</b><p class="muted">Nenhum alerta encontrado.</p></div>' : '')}
    ${temConteudo && !deFeedback ? `<div class="envio-bloco"><b>Arquivo</b><p class="envio-links">
        <a href="/api/conhecimento/envios/${e.id}/arquivo?qual=enviado" target="_blank" rel="noopener">Abrir ${e.convertido ? 'PDF (como o agente lê)' : 'arquivo'}</a>
        ${e.convertido && ['pendente', 'publicando'].includes(e.status) ? ` · <a href="/api/conhecimento/envios/${e.id}/arquivo?qual=original">Baixar original (.${esc(e.arquivo_ext)})</a>` : ''}
      </p></div>
      ` : ''}
    ${temConteudo ? `<div class="envio-bloco"><b>${rotuloTrechos(e, deFeedback)}</b><div class="trechos" id="envio-trechos"><p class="muted">Carregando…</p></div></div>` : ''}
    <details class="envio-bloco linha-tempo"><summary><b>Histórico</b></summary><ul>${(e.eventos || []).map((v) => `<li><span class="muted">${esc(dataHora(v.quando))}</span> ${esc(ACAO_CONH[v.acao] || v.acao)} · ${esc(v.autor)}${v.detalhe ? ` — ${esc(v.detalhe)}` : ''}</li>`).join('')}</ul></details>`;
  if (deFeedback) renderMarkdown($('.resposta-original', box), e.resposta_original || '');
  if ($('#aprov-correcao', box)) $('#aprov-correcao', box).value = e.resposta_corrigida || '';
  if (temConteudo) carregarTrechos(e.id, 1);

  const acoes = $('#envio-acoes');
  acoes.innerHTML = '';
  const botao = (texto, classe, fn) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `btn ${classe}`;
    b.textContent = texto;
    b.addEventListener('click', () => fn(b));
    acoes.appendChild(b);
  };
  if (e.pode_cancelar) botao('Cancelar envio', 'btn-texto', () => decidir(e, 'cancelar'));
  if (e.pode_administrar) {
    if (e.status === 'aprovado') botao('Desativar', 'btn-texto', () => decidir(e, 'desativar'));
    if (e.status === 'desativado') botao('Reativar', 'btn-texto', () => decidir(e, 'reativar'));
    botao('Excluir', 'btn-perigo', () => decidir(e, 'excluir'));
  }
  if (e.pode_decidir) {
    botao('Rejeitar', 'btn-perigo', () => decidir(e, 'rejeitar'));
    botao(e.username === estado.usuario.username ? 'Aprovar (autoaprovação)' : 'Aprovar', 'btn-primario', () => decidir(e, 'aprovar'));
  }
  botao('Fechar', 'btn-texto', () => $('#dlg-envio').close());
}

async function carregarTrechos(id, pagina) {
  const box = $('#envio-trechos');
  if (!box) return;
  try {
    const r = await getJSON(`/api/conhecimento/envios/${id}/trechos?pagina=${pagina}`);
    if (envioAberto !== id) return;
    if (pagina === 1) box.innerHTML = r.itens.length ? '' : '<p class="muted">Nenhum trecho.</p>';
    else box.querySelector('.mais-trechos')?.remove();
    for (const t of r.itens) {
      const d = document.createElement('div');
      d.className = 'trecho';
      d.innerHTML = `<span class="trecho-num">#${t.posicao}</span>`;
      const p = document.createElement('p');
      p.textContent = t.texto;  // texto do arquivo: nunca vira HTML
      d.appendChild(p);
      box.appendChild(d);
    }
    if (r.mais) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-texto mais-trechos';
      b.textContent = `Ver mais trechos${r.total ? ` (${r.total} no total)` : ''}`;
      b.addEventListener('click', () => carregarTrechos(id, pagina + 1));
      box.appendChild(b);
    }
  } catch (err) {
    box.innerHTML = '';
    box.appendChild(erroBox(err.message));
  }
}

const TEXTOS_DECISAO = {
  aprovar: { titulo: 'Aprovar este conhecimento?', texto: 'O arquivo entra na base do agente e passa a ser usado nas respostas. Comentário (opcional):', ok: 'Aprovar', obrigatorio: false },
  rejeitar: { titulo: 'Rejeitar este envio?', texto: 'Quem enviou vai ver o motivo. Motivo (obrigatório):', ok: 'Rejeitar', obrigatorio: true },
  cancelar: { titulo: 'Cancelar o envio?', texto: 'O arquivo é descartado e sai da fila de aprovação.', ok: 'Cancelar envio', obrigatorio: false, semTexto: true },
  desativar: { titulo: 'Desativar este conhecimento?', texto: 'Ele deixa de ser usado pelo agente na hora, mas fica guardado para reativar. Motivo (obrigatório):', ok: 'Desativar', obrigatorio: true },
  reativar: { titulo: 'Reativar este conhecimento?', texto: 'Ele volta a ser usado pelo agente. Motivo (obrigatório):', ok: 'Reativar', obrigatorio: true },
  excluir: { titulo: 'Excluir definitivamente?', texto: 'O arquivo é apagado da base do agente no Dify e não tem volta. Motivo (obrigatório):', ok: 'Excluir', obrigatorio: true },
};

async function decidir(e, acao) {
  const t = TEXTOS_DECISAO[acao];
  let motivo = '';
  let correcao;
  if (acao === 'aprovar' && e.origem === 'feedback_negativo') {
    correcao = ($('#aprov-correcao')?.value || '').trim();
    if (correcao.length < 10) { toast('Escreva a resposta correta antes de aprovar (pelo menos 10 caracteres).', 5000); $('#aprov-correcao')?.focus(); return; }
  }
  for (;;) {
    const r = await confirmar({ titulo: t.titulo, texto: `${tituloEnvio(e)} — ${t.texto}`, input: !t.semTexto, valor: motivo, ok: t.ok });
    if (r === null) return;
    motivo = t.semTexto ? '' : String(r).trim();
    if (!t.obrigatorio || motivo.length >= 5) break;
    toast('Escreva o motivo (pelo menos 5 caracteres).');
  }
  try {
    const corpo = acao === 'cancelar' ? undefined : { motivo, ...(correcao !== undefined ? { resposta_corrigida: correcao } : {}) };
    const atualizado = await (await api(`/api/conhecimento/envios/${e.id}/${acao}`, { metodo: 'POST', corpo })).json();
    toast({ aprovar: 'Aprovado: já está na base do agente.', rejeitar: 'Envio rejeitado.', cancelar: 'Envio cancelado.',
      desativar: 'Conhecimento desativado.', reativar: 'Conhecimento reativado.', excluir: 'Conhecimento excluído.' }[acao], 4500);
    if ($('#dlg-envio').open) abrirEnvio(atualizado.id);
    trocarAbaConh(abaConh);
  } catch (err) {
    toast(err.message, 6000);
  }
}

/* ---- administração: chave de conhecimento, agentes que recebem e aprovadores ---- */
let configConh = null;

async function carregarConfigConhecimento() {
  try {
    const c = await getJSON('/api/admin/conhecimento/config');
    configConh = c;
    $('#conh-config-status').textContent = c.chave_configurada
      ? `Chave do Dify: ${c.chave_mascarada} (${c.origem === 'tela' ? 'cadastrada aqui' : 'do .env'}, ${c.chave_restrita ? 'restrita' : 'acesso a todas as bases'}) · conversor/OCR: ${c.conversor ? 'ligado' : 'desligado'} · até ${c.max_mb} MB`
      : 'Falta cadastrar a chave de API de Conhecimento do Dify para os agentes poderem receber arquivos.';
    $('#btn-chave-conh').textContent = c.chave_configurada ? 'Trocar chave' : 'Cadastrar chave';
    $('#cartao-conhecimento').classList.toggle('pendente', !c.chave_configurada);
    carregarBasesAdmin(c);
  } catch (err) {
    $('#conh-config-status').textContent = err.message;
  }
}

/* ---- Bases de conhecimento (cadastradas): "+ Adicionar base de conhecimento", como "+ Adicionar agente" ---- */
async function carregarBasesAdmin(c) {
  const lista = $('#lista-bases');
  $('#btn-add-base').disabled = !c.chave_configurada;
  if (!c.chave_configurada) {
    lista.innerHTML = '<p class="muted">Cadastre a chave de conhecimento acima para adicionar bases.</p>';
    return;
  }
  let itens = [];
  try {
    itens = (await getJSON('/api/admin/conhecimento/bases?cadastradas=1')).itens;
  } catch (e) {
    lista.innerHTML = '';
    lista.appendChild(erroBox(e.message));
    return;
  }
  lista.innerHTML = itens.length ? '' : `<p class="muted">Nenhuma base cadastrada ainda. Clique em <b>Adicionar base de conhecimento</b>${c.chave_restrita ? ' (com chave restrita, é obrigatório)' : ''}.</p>`;
  for (const b of itens) {
    const d = document.createElement('div');
    d.className = `agente-linha base-linha${b.indisponivel ? ' inativo' : ''}`;
    d.innerHTML = `<span class="agente-icone grande" aria-hidden="true">📚</span>
      <div class="info">
        <div class="nome">${esc(b.nome)}</div>
        <div class="sub mono">${esc(b.id)}</div>
        <div class="sub">${b.indisponivel ? '<b>A chave não alcança esta base</b>' : (b.agentes.length ? `Usada por: ${esc(b.agentes.join(', '))}` : 'Ainda não usada por nenhum agente')}</div>
      </div>
      <div class="qtd">${b.documentos != null ? `${b.documentos} documento${b.documentos === 1 ? '' : 's'}` : ''}</div>
      <button class="btn-icone rem" aria-label="Remover ${esc(b.nome)}" title="Remover da plataforma">${ICONE.lixo}</button>`;
    $('.rem', d).addEventListener('click', async () => {
      const ok = await confirmar({ titulo: 'Remover base?', texto: `"${b.nome}" sai da lista da plataforma. Nada é apagado no Dify.`, ok: 'Remover' });
      if (ok === null) return;
      try {
        await api(`/api/admin/conhecimento/bases/${encodeURIComponent(b.id)}`, { metodo: 'DELETE' });
        toast('Base removida da plataforma.');
        carregarConfigConhecimento();
      } catch (e) {
        toast(e.message, 6000);
      }
    });
    lista.appendChild(d);
  }
}

function abrirDialogoBase() {
  $('#base-id').value = '';
  $('#base-erro').hidden = true;
  $('#dlg-base').showModal();
  setTimeout(() => $('#base-id').focus(), 30);
}

async function salvarBase(e) {
  e.preventDefault();
  if (e.submitter?.value === 'cancel') { $('#dlg-base').close(); return; }
  const erro = $('#base-erro');
  const btn = $('#base-salvar');
  erro.hidden = true;
  btn.disabled = true;
  btn.textContent = 'Conferindo no Dify…';
  try {
    const r = await enviarJSON('/api/admin/conhecimento/bases', 'POST', { base_id: $('#base-id').value.trim() });
    $('#dlg-base').close();
    toast(`Base "${r.nome}" cadastrada. Agora escolha-a no agente (lápis do agente).`, 6000);
    carregarConfigConhecimento();
  } catch (err) {
    erro.textContent = err.message;
    erro.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Adicionar';
  }
}

function abrirChaveConhecimento() {
  $('#chave-conh').value = '';
  $('#chave-conh-erro').hidden = true;
  $('#dlg-chave-conh').showModal();
  setTimeout(() => $('#chave-conh').focus(), 30);
}

async function salvarChaveConhecimento(e) {
  e.preventDefault();
  if (e.submitter?.value === 'cancel') { $('#dlg-chave-conh').close(); return; }
  const erro = $('#chave-conh-erro');
  const btn = $('#chave-conh-salvar');
  erro.hidden = true;
  btn.disabled = true;
  btn.textContent = 'Testando no Dify…';
  try {
    await enviarJSON('/api/admin/conhecimento/config', 'PUT', { chave: $('#chave-conh').value.trim() });
    $('#dlg-chave-conh').close();
    toast('Chave de conhecimento salva.');
    carregarConfigConhecimento();
  } catch (err) {
    erro.textContent = err.message;
    erro.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Testar e salvar';
  }
}

function botaoAprovaUsuario(u) {
  const nomes = estado.adminAgentes.filter((a) => (u.aprova || []).includes(a.id)).map((a) => a.nome);
  const texto = nomes.length ? `Aprova: ${nomes.length === 1 ? nomes[0] : `${nomes.length} agentes`}` : 'Não aprova';
  return `<button type="button" class="btn-agentes btn-aprova${nomes.length ? ' marcado' : ''}" title="${esc(nomes.length ? `Aprova conhecimento de: ${nomes.join(', ')}` : 'Não aprova conhecimento de nenhum agente')}"
    aria-label="Aprovações de ${esc(u.username)}: ${esc(texto)}"${estado.adminAgentes.length ? '' : ' disabled'}>${esc(texto)}</button>`;
}

/* ---- diálogo do agente: escolher a base de conhecimento do Dify ---- */
let basesDify = [];

async function carregarBasesNoDialogo(a) {
  const sel = $('#agente-base');
  const atual = a?.dataset_id || '';
  $('#agente-base-campo').hidden = !a;
  sel.innerHTML = `<option value="">Criar uma base nova: "Colaborativo - ${esc(a?.nome || 'nome do agente')}"</option>`;
  if (!a) return;
  let podeCriar = true;
  try {
    const r = await getJSON('/api/admin/conhecimento/bases');
    basesDify = r.itens;
    podeCriar = r.pode_criar !== false;
  } catch (e) {
    basesDify = [];
    sel.innerHTML += `<option value="" disabled>(${esc(e.message)})</option>`;
  }
  if (!podeCriar) sel.innerHTML = '';  // chave restrita: só as bases da chave
  for (const b of basesDify) {
    const outros = b.agentes.filter((n) => n !== a.nome);
    const o = document.createElement('option');
    o.value = b.id;
    o.textContent = `${b.nome}${b.documentos != null ? ` · ${b.documentos} documento${b.documentos === 1 ? '' : 's'}` : ''}${outros.length ? ` · usada por ${outros.join(', ')}` : ''}`;
    sel.appendChild(o);
  }
  if (atual && !basesDify.some((b) => b.id === atual)) sel.insertAdjacentHTML('beforeend', `<option value="${esc(atual)}">(base atual, não listada)</option>`);
  sel.value = atual || (!podeCriar && basesDify[0] ? basesDify[0].id : '');
  // base dos feedbacks: vazio = a mesma dos arquivos
  const fb = $('#agente-base-feedback');
  fb.innerHTML = '<option value="">A mesma base dos arquivos</option>';
  for (const b of basesDify) {
    const o = document.createElement('option');
    o.value = b.id;
    o.textContent = b.nome;
    fb.appendChild(o);
  }
  if (a.dataset_feedback_id && !basesDify.some((b) => b.id === a.dataset_feedback_id)) fb.insertAdjacentHTML('beforeend', `<option value="${esc(a.dataset_feedback_id)}">(base atual, não listada)</option>`);
  fb.value = a.dataset_feedback_id || '';
}

/* ---- correção de resposta (👎) ---- */
let correcaoAlvo = null;

function abrirCorrecao(alvo) {
  correcaoAlvo = alvo;
  renderMarkdown($('#correcao-original'), alvo.texto || '');
  $('#correcao-texto').value = '';
  $('#correcao-comentario').value = '';
  $('#correcao-erro').hidden = true;
  $('#dlg-correcao').showModal();
  setTimeout(() => $('#correcao-texto').focus(), 30);
}

async function enviarCorrecao(e) {
  e.preventDefault();
  if (e.submitter?.value === 'cancel') { $('#dlg-correcao').close(); toast('Obrigado! A avaliação foi para aprovação; um aprovador vai escrever a resposta certa.', 5000); return; }
  const erro = $('#correcao-erro');
  const correcao = $('#correcao-texto').value.trim();
  erro.hidden = true;
  if (correcao.length < 10) { erro.textContent = 'Escreva a resposta correta (pelo menos 10 caracteres).'; erro.hidden = false; return; }
  const btn = $('#correcao-enviar');
  btn.disabled = true;
  btn.textContent = 'Enviando…';
  try {
    const a = correcaoAlvo;
    await enviarJSON('/api/conhecimento/correcao', 'POST', { agente_id: a.aid, message_id: a.id, conversation_id: a.cid,
      correcao, comentario: $('#correcao-comentario').value.trim() });
    $('#dlg-correcao').close();
    toast('Correção enviada para aprovação. Obrigado!', 5000);
  } catch (err) {
    erro.textContent = err.message;
    erro.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Enviar correção';
  }
}

/* ---- diálogo: adicionar usuário pelo AD ---- */
let buscaTimer;
let buscaSeq = 0;

function ajustarAgentesNovo() {
  const admin = $('#perfil-novo').value === 'admin';
  const box = $('#agentes-novo');
  for (const i of $$('input', box)) {
    i.disabled = admin;
    if (admin) i.checked = true;
  }
  $('#agentes-novo-dica').textContent = admin
    ? 'Administradores recebem todos os agentes. Depois dá para desmarcar algum na lista de usuários.'
    : 'Marque os agentes que a pessoa vai poder usar.';
}

function abrirDialogoUsuario() {
  $('#busca-ad').value = '';
  $('#perfil-novo').value = 'usuario';
  // com um agente só, já vem marcado; com vários, o admin escolhe
  desenharChecksAgentes($('#agentes-novo'), estado.adminAgentes.length === 1 ? [estado.adminAgentes[0].id] : []);
  ajustarAgentesNovo();
  $('#resultado-ad').innerHTML = '<p class="muted">Digite pelo menos 2 letras.</p>';
  $('#dlg-usuario').showModal();
  setTimeout(() => $('#busca-ad').focus(), 30);
}

async function buscarAD() {
  const q = $('#busca-ad').value.trim();
  const box = $('#resultado-ad');
  if (q.length < 2) {
    box.innerHTML = '<p class="muted">Digite pelo menos 2 letras.</p>';
    return;
  }
  const seq = ++buscaSeq;
  box.innerHTML = '<p class="muted"><span class="spinner" style="display:inline-block;vertical-align:-2px"></span> Pesquisando no AD…</p>';
  try {
    const { itens } = await getJSON(`/api/admin/ad?q=${encodeURIComponent(q)}`);
    if (seq !== buscaSeq) return;
    box.innerHTML = itens.length ? '' : '<p class="muted">Ninguém encontrado no AD (contas desabilitadas não aparecem).</p>';
    for (const a of itens) {
      const d = document.createElement('div');
      d.className = 'res-ad';
      d.innerHTML = `<div><strong>${esc(a.nome || a.username)}</strong><span>${esc(a.username)}${a.departamento ? ` · ${esc(a.departamento)}` : ''}${a.email ? ` · ${esc(a.email)}` : ''}</span></div>`;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = a.cadastrado ? 'btn btn-texto' : 'btn btn-primario';
      b.textContent = a.cadastrado ? 'Já cadastrado' : 'Liberar acesso';
      b.disabled = a.cadastrado;
      b.addEventListener('click', async () => {
        b.disabled = true;
        b.textContent = 'Liberando…';
        const perfil = $('#perfil-novo').value;
        const agentes = idsMarcados($('#agentes-novo'));
        if (perfil !== 'admin' && !agentes.length && estado.adminAgentes.length) {
          const ok = await confirmar({ titulo: 'Liberar sem nenhum agente?', texto: `${a.nome || a.username} vai conseguir entrar, mas não vai ter nenhum agente para conversar até você marcar um.`, ok: 'Liberar assim' });
          if (ok === null) { b.disabled = false; b.textContent = 'Liberar acesso'; return; }
        }
        try {
          await enviarJSON('/api/admin/usuarios', 'POST', { usuario: a.username, perfil, agentes });
          b.className = 'btn btn-texto';
          b.textContent = 'Liberado ✓';
          toast(`${a.nome || a.username} agora tem acesso.`);
          carregarUsuarios();
        } catch (e) {
          b.disabled = false;
          b.textContent = 'Liberar acesso';
          toast(e.message, 4500);
        }
      });
      d.appendChild(b);
      box.appendChild(d);
    }
  } catch (e) {
    if (seq !== buscaSeq) return;
    box.innerHTML = '';
    box.appendChild(erroBox(e.message));
  }
}

/* ---- diálogo genérico (confirmar / renomear) ---- */
function confirmar({ titulo, texto = '', input = false, valor = '', ok = 'Confirmar' }) {
  const dlg = $('#dlg-confirma');
  $('#confirma-titulo').textContent = titulo;
  $('#confirma-texto').textContent = texto;
  $('#confirma-texto').hidden = !texto;
  const inp = $('#confirma-input');
  inp.hidden = !input;
  inp.value = valor || '';
  $('#confirma-ok').textContent = ok;
  dlg.returnValue = '';
  return new Promise((resolve) => {
    dlg.addEventListener('close', () => {
      resolve(dlg.returnValue === 'ok' ? (input ? inp.value : true) : null);
    }, { once: true });
    dlg.showModal();
    if (input) setTimeout(() => { inp.focus(); inp.select(); }, 30);
  });
}

/* ------------------------------------------------------------------ eventos */
function ligarEventos() {
  $('#form-login').addEventListener('submit', enviarLogin);
  $('#ver-senha').addEventListener('click', () => {
    const s = $('#login-senha');
    const mostrar = s.type === 'password';
    s.type = mostrar ? 'text' : 'password';
    $('#ver-senha').setAttribute('aria-label', mostrar ? 'Ocultar senha' : 'Mostrar senha');
  });
  $('#btn-instalar').addEventListener('click', clicarInstalar);

  $('#btn-avatar').addEventListener('click', (e) => {
    e.stopPropagation();
    if ($('#menu-usuario').hidden) { abrirMenu(); atualizarContadorPendentes(); }
    else fecharMenu();
  });
  document.addEventListener('click', (e) => {
    if (!$('#menu-usuario').hidden && !e.target.closest('#menu-usuario')) fecharMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (gravacao && !document.querySelector('dialog[open]')) cancelarGravacao();
      fecharMenu();
      fecharLateral();
    }
  });
  $('#menu-chat').addEventListener('click', () => { fecharMenu(); mostrarChat(); if (ehMobile()) abrirLateral(); });
  $('#menu-admin').addEventListener('click', abrirAdmin);
  $('#menu-enviar').addEventListener('click', () => abrirConhecimento('enviar'));
  $('#menu-aprovacoes').addEventListener('click', () => abrirConhecimento('pendentes'));
  for (const b of $$('.aba-conh')) b.addEventListener('click', () => trocarAbaConh(b.dataset.aba));
  $('#form-envio').addEventListener('submit', enviarConhecimento);
  $('#filtros-aprovados').addEventListener('submit', (e) => { e.preventDefault(); carregarAprovados(); });
  $('#filtros-aprovados').addEventListener('reset', () => setTimeout(carregarAprovados, 0));
  $('#filtros-reg-conh').addEventListener('submit', (e) => { e.preventDefault(); carregarRegistrosConh(); });
  $('#rc-csv').addEventListener('click', exportarRegistrosConh);
  $('#envio-fechar').addEventListener('click', () => $('#dlg-envio').close());
  $('#dlg-envio').addEventListener('close', () => { envioAberto = null; });
  $('#btn-chave-conh').addEventListener('click', abrirChaveConhecimento);
  $('#form-chave-conh').addEventListener('submit', salvarChaveConhecimento);
  $('#btn-add-base').addEventListener('click', abrirDialogoBase);
  $('#form-base').addEventListener('submit', salvarBase);
  $('#form-correcao').addEventListener('submit', enviarCorrecao);
  $('#menu-tema').addEventListener('click', alternarTema);
  $('#menu-sair').addEventListener('click', sair);

  $('#btn-menu').addEventListener('click', () => {
    if ($('#lateral').hidden) mostrarChat();
    abrirLateral();
  });
  $('#veu').addEventListener('click', fecharLateral);
  $('#btn-nova').addEventListener('click', () => novaConversa());
  $('#btn-nova-topo').addEventListener('click', () => novaConversa());
  $('#busca-conversa').addEventListener('input', desenharConversas);
  $('#btn-mais-conversas').addEventListener('click', () => carregarConversas(true));

  const ta = $('#pergunta');
  ta.addEventListener('input', ajustarAltura);
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !window.matchMedia('(pointer: coarse)').matches) {
      e.preventDefault();
      enviarPergunta();
    }
  });
  $('#form-chat').addEventListener('submit', (e) => { e.preventDefault(); enviarPergunta(); });
  $('#btn-parar').addEventListener('click', () => pararGeracao());
  $('#btn-microfone').addEventListener('click', iniciarGravacao);
  $('#btn-anexar').addEventListener('click', () => $('#anexo-input').click());
  $('#anexo-input').addEventListener('change', (e) => {
    adicionarAnexos([...e.target.files]);
    e.target.value = '';
  });
  // colar um print (Ctrl+V) ou arrastar arquivos para a caixa também anexa
  ta.addEventListener('paste', (e) => {
    const arquivos = [...(e.clipboardData?.files || [])];
    if (!arquivos.length || !cfgAnexos()) return;
    e.preventDefault();
    adicionarAnexos(arquivos.map((f, i) => (f.name && f.name !== 'image.png' ? f
      : new File([f], `print-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}${i ? `-${i}` : ''}.${(f.type.split('/')[1] || 'png').replace('jpeg', 'jpg')}`, { type: f.type }))));
  });
  const caixa = $('#compositor-caixa');
  caixa.addEventListener('dragover', (e) => {
    if (!cfgAnexos() || !e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault();
    caixa.classList.add('arrastando');
  });
  caixa.addEventListener('dragleave', () => caixa.classList.remove('arrastando'));
  caixa.addEventListener('drop', (e) => {
    caixa.classList.remove('arrastando');
    if (!cfgAnexos() || !e.dataTransfer?.files?.length) return;
    e.preventDefault();
    adicionarAnexos([...e.dataTransfer.files]);
  });
  $('#btn-concluir').addEventListener('click', concluirGravacao);
  $('#grav-cancelar').addEventListener('click', cancelarGravacao);
  $('#mensagens').addEventListener('scroll', () => {
    const box = $('#mensagens');
    seguirFim = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  }, { passive: true });

  $('#sel-agente').addEventListener('change', (e) => trocarAgente(Number(e.target.value)));
  $('#btn-add-usuario').addEventListener('click', abrirDialogoUsuario);
  $('#perfil-novo').addEventListener('change', ajustarAgentesNovo);
  $('#btn-add-agente').addEventListener('click', () => abrirDialogoAgente());
  $('#form-agente').addEventListener('submit', salvarAgente);
  $('#filtros-registros').addEventListener('submit', (e) => { e.preventDefault(); carregarRegistros(); });
  $('#filtros-registros').addEventListener('reset', () => setTimeout(carregarRegistros, 0));
  $('#filtros-conversas').addEventListener('submit', (e) => { e.preventDefault(); buscarConversasAdmin(); });
  $('#filtros-conversas').addEventListener('reset', () => setTimeout(buscarConversasAdmin, 0));
  $('#conv-todas').addEventListener('change', (e) => marcarTodasConversas(e.target.checked));
  $('#btn-exportar-sel').addEventListener('click', exportarSelecionadas);
  $('#btn-exportar-tudo').addEventListener('click', exportarTodas);
  $('#conv-fechar').addEventListener('click', () => $('#dlg-conversa').close());
  $('#conv-fechar2').addEventListener('click', () => $('#dlg-conversa').close());
  $('#dlg-conversa').addEventListener('close', () => { conversaAberta = null; });
  $('#conv-exportar').addEventListener('click', () => {
    if (conversaAberta) exportarConversas({ itens: [refConversa(conversaAberta)] }, $('#conv-exportar'));
  });
  for (const b of $$('.aba')) b.addEventListener('click', () => trocarAba(b.dataset.aba));
  $('#filtro-usuarios').addEventListener('input', desenharUsuarios);
  $('#busca-ad').addEventListener('input', () => {
    clearTimeout(buscaTimer);
    buscaTimer = setTimeout(buscarAD, 350);
  });
  // Enter dentro dos diálogos não deve fechá-los por engano
  $('#form-busca-ad').addEventListener('submit', (e) => {
    if (e.submitter?.value !== 'cancel') {
      e.preventDefault();
      clearTimeout(buscaTimer);
      buscarAD();
    }
  });
  $('#confirma-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      $('#dlg-confirma').close('ok');
    }
  });

  window.matchMedia('(display-mode: standalone)').addEventListener?.('change', atualizarCardInstalar);
}

/* ------------------------------------------------------------------ início */
async function iniciar() {
  boasVindas = $('#boas-vindas');
  aplicarTema(lerTema());
  ligarEventos();
  ajustarAltura();

  getJSON('/api/config').then((c) => {
    if (c.dominio) $('#dominio').textContent = c.dominio;
  }).catch(() => {});

  try {
    estado.usuario = await getJSON('/api/me');
  } catch {
    estado.usuario = null;
  }
  if (estado.usuario) await entrarApp();
  else mostrarLogin();
}

iniciar();
