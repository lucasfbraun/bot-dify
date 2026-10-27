# Agente Técnico PU · Grupo Flexível

Frontend próprio (PWA) para o agente **Agente Técnico PU(qlik)** do Dify, com a identidade visual do Grupo Flexível, login pelo Active Directory e cadastro de quem pode acessar.

- **Login com o usuário e a senha da rede** (AD `flexivel.local`). Aceita `nome.sobrenome`, `FLEXIVEL\nome.sobrenome` ou `nome.sobrenome@flexivel.local`.
- **Só entra quem está cadastrado** na tela *Usuários e acessos*. O administrador pesquisa a pessoa no AD e libera o acesso com o perfil *Usuário* ou *Administrador*. Desativar ou remover alguém corta o acesso na hora.
- **Registro de acessos**: logins ok, senha errada, pessoa sem liberação, conta desabilitada no AD e bloqueio por tentativas.
- **Chat com streaming**: mostra o que o agente está fazendo (consultando o Qlik, buscando certificados etc.), tem histórico de conversas por pessoa, renomear/apagar, copiar e avaliar (👍/👎 vão para os registros do Dify).
- **Celular**: layout responsivo, instalável como app (PWA). A tela de login mostra um card para instalar (Android: botão *Instalar*; iPhone: instrução *Compartilhar → Adicionar à Tela de Início*).
- **Segurança**: a chave da API do Dify fica só no servidor. A sessão usa cookie HttpOnly + SameSite=Strict. Há proteção CSRF, CSP restritiva e bloqueio após 5 senhas erradas (10 min).

## Estrutura

```
backend/app/        FastAPI: login LDAP, cadastro (SQLite em /data), proxy da API do Dify
frontend/           PWA (HTML/CSS/JS sem build), ícones e logo
deploy/             exemplo de configuração do nginx (HTTPS + streaming)
tests/              AD falso, Dify falso e teste ponta a ponta da API
Dockerfile, docker-compose.yml, .env.example
```

## Deploy no servidor do Dify (10.100.100.13)

1. **DNS e certificado**: crie o registro `agente.grupoflexivel.com.br` apontando para o servidor e tenha um certificado válido para ele (o PWA só instala com HTTPS).
2. **Chave da API do Dify**: no Dify, abra o app *Agente Técnico PU(qlik)* → **Acesso à API** → **Chave da API** → **Criar nova chave**. Copie a chave `app-...`.
3. **Código no servidor**:
   ```bash
   git clone https://github.com/lucasfbraun/bot-dify.git /opt/agente-pu-web
   cd /opt/agente-pu-web
   cp .env.example .env
   chmod 600 .env
   nano .env
   ```
   No `.env`, preencha estes campos:
   - `SECRET_KEY`: gere com `openssl rand -base64 48`.
   - `INITIAL_ADMINS`: o seu login da rede.
   - `LDAP_BIND_PASSWORD`: a senha da conta de serviço.
   - `DIFY_API_KEY`: a chave do passo 2.
4. **Subir o container**:
   ```bash
   docker compose up -d --build
   docker compose logs -f
   ```
   O app fica em `127.0.0.1:8090`, acessível só pela própria máquina.
5. **HTTPS / proxy**, escolha conforme o servidor:
   - **Opção A, nginx instalado no host**: copie `deploy/nginx-agente.conf` para `/etc/nginx/conf.d/`, ajuste os certificados e rode `nginx -t && systemctl reload nginx`.
   - **Opção B, HTTPS feito pelo nginx do próprio Dify (container)**:
     1. Descomente a seção `networks` no `docker-compose.yml`. A rede do Dify costuma ser `docker_default`; confira com `docker network ls`.
     2. Adicione o bloco `server` do `deploy/nginx-agente.conf` em `dify/docker/nginx/conf.d/` com `proxy_pass http://agente-pu-web:8000;`.
     3. Reinicie o nginx do Dify.
     4. Nessa opção, você também pode usar `DIFY_API_URL=http://api:5001/v1`, que fala com o Dify direto pela rede interna.
6. Acesse `https://agente.grupoflexivel.com.br`, entre com o seu login (vira administrador) e libere as demais pessoas em **menu do avatar → Usuários e acessos**.

### LDAP: recomendações

- Na porta 389 sem TLS, a senha de quem faz login trafega **sem criptografia** até o AD. Se o AD tiver certificado, prefira LDAPS: `LDAP_PORT=636` e `LDAP_USE_SSL=true`. A alternativa é `LDAP_START_TLS=true` na 389. Se o AD exigir *LDAP signing*, o bind simples na 389 sem TLS é recusado, e então o TLS é obrigatório.
- A conta de serviço precisa só de leitura no AD.
- A senha da conta de serviço deve ficar **apenas no `.env` do servidor**. Como ela já circulou em texto, troque essa senha no AD e atualize o `.env`.

### Atualizar

```bash
cd /opt/agente-pu-web && git pull && docker compose up -d --build
```

O cadastro de usuários e o registro de acessos ficam no volume `agente-pu-dados` e não se perdem ao atualizar.

## Testar localmente (sem AD e sem Dify)

```bash
pip install -r backend/requirements.txt
python tests/dev_server.py            # app em http://127.0.0.1:8088 (AD e Dify falsos)
python tests/test_api.py              # em outro terminal: teste ponta a ponta
```

Usuários do AD falso, todos com a senha `Teste@123`:

- `admin.ti`: admin inicial.
- `maria.silva` e `carlos.souza`: precisam ser liberados pelo admin.
- `joao.desligado`: conta desabilitada.
