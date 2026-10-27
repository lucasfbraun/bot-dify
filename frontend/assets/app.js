/* Agente Técnico PU · Grupo Flexível — frontend (SPA + PWA) */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const ICONE = {
  copiar: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3h11a2 2 0 012 2v11h-2V5H8zM5 7h10a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V9a2 2 0 012-2zm0 2v10h10V9z"/></svg>',
  ok: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 16.2l-3.5-3.5L4 14.2l5 5 11-11-1.5-1.5z"/></svg>',
  like: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 21h4V9H2zm20-11a2 2 0 00-2-2h-6.3l1-4.6v-.3a1.5 1.5 0 00-.4-1L13.2 1 6.6 7.6A2 2 0 006 9v10a2 2 0 002 2h9a2 2 0 001.8-1.2l3-7.1c.1-.2.2-.5.2-.7z"/></svg>',
  dislike: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 3h-4v12h4zM2 14a2 2 0 002 2h6.3l-1 4.6v.3a1.5 1.5 0 00.4 1l1.1 1.1 6.6-6.6A2 2 0 0018 15V5a2 2 0 00-2-2H7a2 2 0 00-1.8 1.2l-3 7.1c-.1.2-.2.5-.2.7z"/></svg>',
  editar: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 17.3V21h3.7L17.8 9.9l-3.7-3.7zM20.7 7a1 1 0 000-1.4l-2.3-2.3a1 1 0 00-1.4 0l-1.8 1.8 3.7 3.7z"/></svg>',
  lixo: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 1h4v2H4V4h4zM5 7h14l-1 14H6zm4 2v10h2V9zm4 0v10h2V9z"/></svg>',
};

const SUGESTOES_PADRAO = [
  { cat: 'Produto', txt: 'Tudo sobre o FLEXX AG 2032' },
  { cat: 'Qualidade', txt: 'Analise a última reclamação de cliente e as inspeções do lote envolvido' },
  { cat: 'Engenharia e custo', txt: 'Formulação e custo do FLEXX TH T190AH1' },
  { cat: 'Compras', txt: 'De quem compramos o TDI 8020 nos últimos 12 meses e a que preço?' },
  { cat: 'Desenvolvimento', txt: 'Já fizemos desenvolvimento com poliol vegetal?' },
];

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

const estado = {
  usuario: null,
  info: null,
  conversaAtual: null,
  conversas: [],
  maisConversas: false,
  gerando: false,
  taskId: null,
  abort: null,
  usuarios: [],
};

/* ------------------------------------------------------------------ utilidades */
class ErroApi extends Error {
  constructor(status, mensagem) { super(mensagem); this.status = status; }
}

async function api(caminho, { metodo = 'GET', corpo, sinal } = {}) {
  const opts = { method: metodo, credentials: 'same-origin', headers: { 'X-Requested-With': 'agente-pu' }, signal: sinal };
  if (corpo !== undefined) {
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

  const inicial = new URLSearchParams(location.search).get('c');
  const irParaAdmin = location.pathname === '/admin' && u.perfil === 'admin';
  limparChat();
  if (irParaAdmin) abrirAdmin();
  else mostrarChat();

  carregarInfo();
  await carregarConversas();
  if (inicial && !irParaAdmin) abrirConversa(inicial);
}

function atualizarSaudacao() {
  const h = new Date().getHours();
  const periodo = h < 12 ? 'Bom dia' : h < 18 ? 'Boa tarde' : 'Boa noite';
  const nome = primeiroNome(estado.usuario?.nome, estado.usuario?.username);
  $('#saudacao').textContent = nome ? `${periodo}, ${nome}!` : `${periodo}!`;
}

async function carregarInfo() {
  try {
    estado.info = await getJSON('/api/agente/info');
  } catch {
    estado.info = null;
  }
  if (estado.info?.abertura) $('#abertura').textContent = estado.info.abertura.replace(/[*_#`]/g, '').trim();
  desenharSugestoes();
}

function desenharSugestoes() {
  const box = $('#sugestoes');
  const doDify = (estado.info?.sugestoes || []).filter(Boolean).map((t) => ({ cat: '', txt: t }));
  const lista = doDify.length ? doDify : SUGESTOES_PADRAO;
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
  try {
    const ultimo = mais && estado.conversas.length ? estado.conversas[estado.conversas.length - 1].id : null;
    const r = await getJSON(`/api/conversas${ultimo ? `?last_id=${encodeURIComponent(ultimo)}` : ''}`);
    estado.conversas = mais ? estado.conversas.concat(r.itens) : r.itens;
    estado.maisConversas = r.mais;
  } catch (e) {
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
    nav.innerHTML = `<p class="muted" style="padding:8px 10px;font-size:14px">${filtro ? 'Nenhuma conversa encontrada.' : 'Suas conversas aparecem aqui.'}</p>`;
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
    const r = await enviarJSON(`/api/conversas/${encodeURIComponent(c.id)}`, 'PATCH', { nome: nome.trim() });
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
    await api(`/api/conversas/${encodeURIComponent(c.id)}`, { metodo: 'DELETE' });
    estado.conversas = estado.conversas.filter((x) => x.id !== c.id);
    if (estado.conversaAtual === c.id) novaConversa();
    desenharConversas();
    toast('Conversa apagada.');
  } catch (e) {
    toast(e.message);
  }
}

function limparChat() {
  estado.conversaAtual = null;
  const box = $('#mensagens');
  box.innerHTML = '';
  box.appendChild(boasVindas);
  atualizarSaudacao();
  marcarConversaAtiva();
}

function novaConversa(focar = true) {
  pararGeracao(true);
  limparChat();
  history.replaceState(null, '', '/');
  fecharLateral();
  mostrarChat();
  if (focar && !ehMobile()) $('#pergunta').focus();
}

async function abrirConversa(id, primeiroId = null) {
  pararGeracao(true);
  fecharLateral();
  const box = $('#mensagens');
  if (!primeiroId) {
    estado.conversaAtual = id;
    history.replaceState(null, '', `/?c=${encodeURIComponent(id)}`);
    marcarConversaAtiva();
    box.innerHTML = '<div class="msg msg-bot"><div class="conteudo"><span class="status"><span class="spinner"></span>Carregando a conversa…</span></div></div>';
  }
  try {
    const r = await getJSON(`/api/conversas/${encodeURIComponent(id)}/mensagens${primeiroId ? `?first_id=${encodeURIComponent(primeiroId)}` : ''}`);
    if (estado.conversaAtual !== id) return;
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
      frag.appendChild(bolhaUsuario(m.pergunta));
      const bot = bolhaBot();
      renderMarkdown(bot.md, m.resposta);
      finalizarBot(bot, { id: m.id, texto: m.resposta, avaliacao: m.avaliacao, fontes: m.fontes });
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
    if (e.status === 401) return;
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

function bolhaUsuario(texto) {
  const d = document.createElement('div');
  d.className = 'msg msg-user';
  const b = document.createElement('div');
  b.className = 'bolha';
  b.textContent = texto;
  d.appendChild(b);
  return d;
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

function finalizarBot(bot, { id, texto, avaliacao, fontes }) {
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
    ${id ? `<button class="btn-icone${avaliacao === 'like' ? ' on' : ''}" data-a="like" aria-label="Resposta útil" title="Resposta útil">${ICONE.like}</button>
    <button class="btn-icone${avaliacao === 'dislike' ? ' on' : ''}" data-a="dislike" aria-label="Resposta ruim" title="Resposta ruim">${ICONE.dislike}</button>` : ''}`;
  acoes.addEventListener('click', async (ev) => {
    const b = ev.target.closest('button');
    if (!b) return;
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
    try {
      await enviarJSON(`/api/mensagens/${encodeURIComponent(id)}/feedback`, 'POST', { avaliacao: nova });
      for (const x of $$('[data-a="like"],[data-a="dislike"]', acoes)) x.classList.toggle('on', x.dataset.a === nova);
      if (nova) toast(nova === 'like' ? 'Obrigado pela avaliação!' : 'Obrigado! Vamos usar isso para melhorar o agente.');
    } catch (e) {
      toast(e.message);
    }
  });
  bot.conteudo.appendChild(acoes);
}

let seguirFim = true;
function rolarFim(forcar = false) {
  const box = $('#mensagens');
  if (forcar || seguirFim) box.scrollTop = box.scrollHeight;
}

/* ------------------------------------------------------------------ envio + streaming */
function ajustarAltura() {
  const ta = $('#pergunta');
  ta.style.height = 'auto';
  // vazio ou ainda oculto: deixa a altura natural (rows=1) para não cortar o placeholder
  if (ta.value && ta.scrollHeight) ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  $('#btn-enviar').disabled = !ta.value.trim() || estado.gerando;
}

function modoGerando(sim) {
  estado.gerando = sim;
  $('#btn-enviar').hidden = sim;
  $('#btn-parar').hidden = !sim;
  ajustarAltura();
}

async function pararGeracao(silencioso = false) {
  if (!estado.gerando) return;
  const task = estado.taskId;
  const ctrl = estado.abort;
  if (task) {
    try { await api(`/api/chat/${encodeURIComponent(task)}/parar`, { metodo: 'POST' }); } catch { /* segue */ }
  }
  // se o Dify não encerrar o stream sozinho, corta do lado de cá
  setTimeout(() => ctrl?.abort(), silencioso ? 0 : 2500);
}

async function enviarPergunta() {
  const ta = $('#pergunta');
  const pergunta = ta.value.trim();
  if (!pergunta || estado.gerando) return;
  ta.value = '';
  ajustarAltura();
  if (ehMobile()) ta.blur();

  const box = $('#mensagens');
  if (boasVindas.isConnected) boasVindas.remove();
  box.appendChild(bolhaUsuario(pergunta));
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
  modoGerando(true);

  const conversaEnviada = estado.conversaAtual;
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
    const r = await api('/api/chat', { metodo: 'POST', corpo: { pergunta, conversation_id: conversaEnviada }, sinal: ctrl.signal });
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
              messageId = ev.message_id || messageId;
              if (!estado.conversaAtual && ev.conversation_id) {
                estado.conversaAtual = ev.conversation_id;
                history.replaceState(null, '', `/?c=${encodeURIComponent(ev.conversation_id)}`);
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
  }

  status.remove();
  renderMarkdown(bot.md, texto);
  if (!texto && !erro) {
    bot.md.innerHTML = `<p class="muted">${terminou ? 'O agente não retornou texto.' : 'Resposta interrompida.'}</p>`;
  } else if (!terminou && !erro && texto) {
    bot.md.insertAdjacentHTML('beforeend', '<p class="muted"><em>(resposta interrompida)</em></p>');
  }
  if (erro) bot.conteudo.appendChild(erroBox(erro));
  finalizarBot(bot, { id: messageId, texto, fontes });
  estado.taskId = null;
  estado.abort = null;
  modoGerando(false);
  rolarFim();

  if (estado.usuario) {
    const nova = !conversaEnviada && estado.conversaAtual;
    await carregarConversas();
    marcarConversaAtiva();
    // o nome automático da conversa costuma chegar um pouco depois
    if (nova) setTimeout(() => carregarConversas(), 3500);
  }
}

/* ------------------------------------------------------------------ navegação chat/admin */
function mostrarChat() {
  $('#view-admin').hidden = true;
  $('#view-chat').hidden = false;
  $('#lateral').hidden = false;
  if (location.pathname === '/admin') history.replaceState(null, '', estado.conversaAtual ? `/?c=${encodeURIComponent(estado.conversaAtual)}` : '/');
}

function abrirAdmin() {
  if (estado.usuario?.perfil !== 'admin') return;
  fecharMenu();
  fecharLateral();
  $('#view-chat').hidden = true;
  $('#lateral').hidden = true;
  $('#view-admin').hidden = false;
  history.replaceState(null, '', '/admin');
  trocarAba('usuarios');
  carregarUsuarios();
}

function trocarAba(aba) {
  for (const b of $$('.aba')) {
    const ativa = b.dataset.aba === aba;
    b.classList.toggle('ativa', ativa);
    b.setAttribute('aria-selected', String(ativa));
  }
  $('#aba-usuarios').hidden = aba !== 'usuarios';
  $('#aba-acessos').hidden = aba !== 'acessos';
  if (aba === 'acessos') carregarAcessos();
}

/* ------------------------------------------------------------------ administração */
async function carregarUsuarios() {
  const lista = $('#lista-usuarios');
  if (!estado.usuarios.length) lista.innerHTML = '<p class="muted">Carregando…</p>';
  try {
    estado.usuarios = (await getJSON('/api/admin/usuarios')).itens;
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
      <select aria-label="Perfil de ${esc(u.username)}">
        <option value="usuario"${u.perfil === 'usuario' ? ' selected' : ''}>Usuário</option>
        <option value="admin"${u.perfil === 'admin' ? ' selected' : ''}>Administrador</option>
      </select>
      <label class="chave"><input type="checkbox"${u.ativo ? ' checked' : ''}><span>${u.ativo ? 'Ativo' : 'Inativo'}</span></label>
      <button class="btn-icone rem" aria-label="Remover ${esc(u.username)}" title="Remover acesso">${ICONE.lixo}</button>`;

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
        const ok = await confirmar({ titulo: 'Desativar acesso?', texto: `${u.nome || u.username} não conseguirá mais entrar no Agente Técnico PU (o histórico é mantido).`, ok: 'Desativar' });
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
    toast('Alteração salva.');
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

async function carregarAcessos() {
  const lista = $('#lista-acessos');
  lista.innerHTML = '<p class="muted">Carregando…</p>';
  try {
    const { itens } = await getJSON('/api/admin/acessos');
    lista.innerHTML = itens.length ? '' : '<p class="muted">Nenhum acesso registrado ainda.</p>';
    for (const a of itens) {
      const [rotulo, classe] = RESULTADO_ACESSO[a.resultado] || [a.resultado, 'falha'];
      const d = document.createElement('div');
      d.className = 'acesso';
      d.innerHTML = `<span>${esc(dataHora(a.quando))}</span><span>${esc(a.username || '—')}</span>`
        + `<span class="${classe}" title="${esc(a.detalhe || '')}">${esc(rotulo)}</span><span class="muted">${esc(a.ip || '')}</span>`;
      lista.appendChild(d);
    }
  } catch (e) {
    lista.innerHTML = '';
    lista.appendChild(erroBox(e.message));
  }
}

/* ---- diálogo: adicionar usuário pelo AD ---- */
let buscaTimer;
let buscaSeq = 0;

function abrirDialogoUsuario() {
  $('#busca-ad').value = '';
  $('#perfil-novo').value = 'usuario';
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
        try {
          await enviarJSON('/api/admin/usuarios', 'POST', { usuario: a.username, perfil: $('#perfil-novo').value });
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
    if ($('#menu-usuario').hidden) abrirMenu();
    else fecharMenu();
  });
  document.addEventListener('click', (e) => {
    if (!$('#menu-usuario').hidden && !e.target.closest('#menu-usuario')) fecharMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      fecharMenu();
      fecharLateral();
    }
  });
  $('#menu-chat').addEventListener('click', () => { fecharMenu(); mostrarChat(); if (ehMobile()) abrirLateral(); });
  $('#menu-admin').addEventListener('click', abrirAdmin);
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
  $('#mensagens').addEventListener('scroll', () => {
    const box = $('#mensagens');
    seguirFim = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  }, { passive: true });

  $('#btn-add-usuario').addEventListener('click', abrirDialogoUsuario);
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
