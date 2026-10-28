# Agentes do Grupo Flexível · frontend para o Dify

Frontend próprio (PWA) para os agentes de conversa do Dify, com a identidade visual do Grupo Flexível, login pelo Active Directory e controle de **quem usa qual agente**.

- **Vários agentes do Dify numa tela só.** O administrador cadastra cada agente colando a chave de API dele, e a plataforma busca no Dify o nome, a descrição e o ícone. Por exemplo, o *Agente Técnico PU(qlik)* (acesso completo às bases) e um agente com menos acesso para outras áreas.
- **Cada usuário só vê os agentes liberados para ele.** No cadastro, o admin marca os agentes de cada pessoa. A permissão é conferida a cada requisição no servidor, então tirar a marca corta o acesso na hora, até com a tela aberta.
- **Administradores recebem todos os agentes**, inclusive os cadastrados depois, e podem desmarcar os que não querem usar no chat. Na tela de administração, eles continuam vendo todos.
- **Login com o usuário e a senha da rede** (AD `flexivel.local`). Aceita `nome.sobrenome`, `FLEXIVEL\nome.sobrenome` ou `nome.sobrenome@flexivel.local`. Só entra quem está cadastrado.
- **Registro de acessos**: logins ok, senha errada, pessoa sem liberação, conta desabilitada no AD e bloqueio por tentativas.
- **Chat com streaming**: seletor de agente na barra lateral (e atalhos na tela inicial), histórico separado por agente, status do que o agente está fazendo (consultando o Qlik, buscando certificados etc.), renomear/apagar, copiar e avaliar (👍/👎 vão para os registros do Dify).
- **Celular**: layout responsivo, instalável como app (PWA). A tela de login mostra um card para instalar (Android: botão *Instalar*; iPhone: instrução *Compartilhar → Adicionar à Tela de Início*).
- **Segurança**: as chaves de API do Dify ficam só no servidor (no banco local) e nunca vão para o navegador. A tela de administração mostra só o começo e o fim da chave. A sessão usa cookie HttpOnly + SameSite=Strict. Há proteção CSRF, CSP restritiva e bloqueio após 5 senhas erradas (10 min).

## Estrutura

```
backend/app/        FastAPI: login LDAP, cadastro de usuários e agentes (SQLite em /data), proxy da API do Dify
frontend/           PWA (HTML/CSS/JS sem build), ícones e logo
deploy/             exemplo de configuração do nginx (HTTPS + streaming)
tests/              AD falso, Dify falso (vários apps), testes da API, da migração e da interface
Dockerfile, docker-compose.yml, .env.example
```

## Agentes

### Cadastrar um agente

1. No Dify, abra o app → **Acesso à API** → **Chave da API** → **Criar nova chave** e copie a chave `app-...`.
2. Na plataforma: **menu do avatar → Administração → aba Agentes → Adicionar agente**. Cole a chave e clique em **Verificar e cadastrar**.
3. A plataforma confere a chave no Dify e preenche nome, descrição, tipo e ícone. Todos os administradores já recebem o agente.
4. Para liberar o agente para outras pessoas, vá na aba **Usuários** e clique no botão de agentes da linha da pessoa (por exemplo, *Nenhum agente* ou *1 de 2 agentes*).

Só servem apps de **conversa**: *Chatbot*, *Agente* e *Chatflow*. Apps do tipo *Workflow* ou *Gerador de texto* são recusados com uma mensagem explicando o motivo.

### Editar, desativar e remover

- **Editar**: muda o nome exibido, a descrição da tela inicial e as perguntas de exemplo (uma por linha, opcional `Categoria | pergunta`). Se o app tiver perguntas sugeridas configuradas no Dify, elas têm prioridade.
- **Trocar a chave**: no mesmo diálogo, cole uma chave nova **do mesmo app** (por exemplo, depois de revogar a antiga no Dify). As conversas pertencem ao app no Dify: com a chave de outro app, o histórico antigo deixa de aparecer.
- **Desativar**: o agente some do chat de todos, mas as liberações ficam guardadas para quando ele for reativado.
- **Remover**: tira o agente da plataforma e de todas as pessoas. As conversas continuam no Dify; se a mesma chave for cadastrada de novo, elas voltam a aparecer para quem tiver acesso.

### Como as permissões funcionam

| Situação | O que acontece |
|---|---|
| Usuário novo (perfil *Usuário*) | Recebe só os agentes marcados no cadastro. Sem nenhum, consegue entrar mas vê *Nenhum agente liberado*. |
| Usuário novo (perfil *Administrador*) | Recebe todos os agentes. |
| Alguém vira administrador | Recebe todos os agentes que ainda não tinha. |
| Agente novo cadastrado | Vai automaticamente para todos os administradores. Os demais precisam ser marcados. |
| Admin desmarca um agente para si | Deixa de vê-lo no chat, mas continua administrando o agente. |
| Acesso tirado com a tela aberta | Na próxima ação, a pessoa vê o aviso e a tela passa para os agentes que sobraram. |

Cada pessoa é identificada no Dify como `ad:<login>`. As conversas de cada agente ficam no app correspondente do Dify, então cada agente tem o próprio histórico e ninguém vê conversa de outra pessoa.

## Deploy no servidor do Dify (10.100.100.13)

1. **DNS e certificado**: crie o registro `agente.grupoflexivel.com.br` apontando para o servidor e tenha um certificado válido para ele (o PWA só instala com HTTPS).
2. **Código no servidor**:
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
   - `DIFY_API_KEY`: opcional. Se preenchida, essa chave vira o primeiro agente na primeira subida. Os outros agentes são cadastrados pela tela de administração.
3. **Subir o container**:
   ```bash
   docker compose up -d --build
   docker compose logs -f
   ```
   O app fica em `127.0.0.1:8090`, acessível só pela própria máquina.
4. **HTTPS / proxy**, escolha conforme o servidor:
   - **Opção A, nginx instalado no host**: copie `deploy/nginx-agente.conf` para `/etc/nginx/conf.d/`, ajuste os certificados e rode `nginx -t && systemctl reload nginx`.
   - **Opção B, HTTPS feito pelo nginx do próprio Dify (container)**:
     1. Descomente a seção `networks` no `docker-compose.yml`. A rede do Dify costuma ser `docker_default`; confira com `docker network ls`.
     2. Adicione o bloco `server` do `deploy/nginx-agente.conf` em `dify/docker/nginx/conf.d/` com `proxy_pass http://agente-pu-web:8000;`.
     3. Reinicie o nginx do Dify.
     4. Nessa opção, você também pode usar `DIFY_API_URL=http://api:5001/v1`, que fala com o Dify direto pela rede interna.
5. Acesse `https://agente.grupoflexivel.com.br`, entre com o seu login (vira administrador), cadastre os agentes em **Administração → Agentes** e libere as pessoas na aba **Usuários**.

### LDAP: recomendações

- Na porta 389 sem TLS, a senha de quem faz login trafega **sem criptografia** até o AD. Se o AD tiver certificado, prefira LDAPS: `LDAP_PORT=636` e `LDAP_USE_SSL=true`. A alternativa é `LDAP_START_TLS=true` na 389. Se o AD exigir *LDAP signing*, o bind simples na 389 sem TLS é recusado, e então o TLS é obrigatório.
- A conta de serviço precisa só de leitura no AD.
- A senha da conta de serviço deve ficar **apenas no `.env` do servidor**. Como ela já circulou em texto, troque essa senha no AD e atualize o `.env`.

### Atualizar

```bash
cd /opt/agente-pu-web && git pull && docker compose up -d --build
```

O `--build` é obrigatório: o frontend é copiado para dentro da imagem, então só o `git pull` não muda o que o navegador recebe. O cadastro de usuários, os agentes e o registro de acessos ficam no volume `agente-pu-dados` e não se perdem ao atualizar. Não use `docker compose down -v`, porque o `-v` apaga esse volume.

#### Atualizando da versão de agente único

Na primeira subida desta versão, a migração faz o seguinte, sem precisar de nenhuma ação manual:

1. A chave que está em `DIFY_API_KEY` vira o primeiro agente, com o nome lido do Dify (por exemplo, *Agente Técnico PU(qlik)*) e as perguntas de exemplo que já apareciam na tela inicial.
2. **Todos os usuários que já existiam continuam com acesso a ele.** Depois, tire o agente de quem não deve usá-lo na aba **Usuários**.
3. A migração roda uma vez só. Se você remover o agente depois, ele não volta sozinho no próximo reinício.

Se o Dify estiver fora do ar na hora da subida, o agente é cadastrado com o nome padrão (`APP_NAME`); dá para renomear depois em **Editar**. Os navegadores baixam o frontend novo sozinhos (a versão do cache do app mudou); se algo parecer antigo, recarregue com Ctrl+Shift+R.

## Testar localmente (sem AD e sem Dify)

```bash
pip install -r backend/requirements.txt
python tests/dev_server.py            # app em http://127.0.0.1:8088 (AD e Dify falsos)
python tests/test_api.py              # em outro terminal: teste ponta a ponta da API
python tests/test_migracao.py         # não precisa do servidor: atualização de um banco da versão antiga
pip install playwright && python tests/test_ui.py   # interface no Chrome (prints em tests/prints/)
```

`test_api.py` e `test_ui.py` esperam um servidor recém-iniciado: reinicie o `dev_server.py` entre um e outro.

O Dify falso tem três apps:

- **Agente Técnico PU(qlik)**: agente com ferramentas. A chave dele é a `DIFY_API_KEY` padrão, então vira o primeiro agente na subida.
- **Agente Comercial**: chatflow sem ferramentas, com a chave `app-Comercial0000000000000`.
- **Fluxo de relatórios**: workflow que deve ser recusado no cadastro, com a chave `app-Fluxo00000000000000000`.

Usuários do AD falso, todos com a senha `Teste@123`:

- `admin.ti`: admin inicial.
- `maria.silva` e `carlos.souza`: precisam ser liberados pelo admin.
- `joao.desligado`: conta desabilitada.
