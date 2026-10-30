/* Service worker dos agentes do Grupo Flexível: deixa o app instalável e abre rápido.
   Nunca guarda nada de /api (respostas e dados ficam só no servidor). */
const VERSAO = 'agente-pu-v10';
const SHELL = [
  '/',
  '/assets/app.css?v=9',
  '/assets/app.js?v=9',
  '/assets/vendor/marked.min.js',
  '/assets/vendor/purify.min.js',
  '/icons/logo-flexivel.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/favicon-32.png',
  '/icons/apple-touch-icon.png',
  '/manifest.webmanifest',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSAO).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(nomes.filter((n) => n !== VERSAO).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;

  // páginas: sempre tenta a rede (pega versão nova); sem rede, abre a casca guardada
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).then((r) => {
        const copia = r.clone();
        caches.open(VERSAO).then((c) => c.put('/', copia));
        return r;
      }).catch(() => caches.match('/')),
    );
    return;
  }

  // arquivos estáticos: cache primeiro, atualiza em segundo plano
  e.respondWith(
    caches.match(req).then((guardado) => {
      const rede = fetch(req).then((r) => {
        if (r.ok) {
          const copia = r.clone();
          caches.open(VERSAO).then((c) => c.put(req, copia));
        }
        return r;
      }).catch(() => guardado || Response.error());
      return guardado || rede;
    }),
  );
});
