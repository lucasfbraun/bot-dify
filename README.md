# Agentes do Grupo Flexível · frontend para o Dify

Frontend próprio (PWA) para os agentes de conversa do Dify, com a identidade visual do Grupo Flexível, login pelo Active Directory e controle de **quem usa qual agente**.

- **Vários agentes do Dify numa tela só.** O administrador cadastra cada agente colando a chave de API dele, e a plataforma busca no Dify o nome, a descrição e o ícone. Por exemplo, o *Agente Técnico PU(qlik)* (acesso completo às bases) e um agente com menos acesso para outras áreas.
- **Cada usuário só vê os agentes liberados para ele.** No cadastro, o admin marca os agentes de cada pessoa. A permissão é conferida a cada requisição no servidor, então tirar a marca corta o acesso na hora, até com a tela aberta.
- **Administradores recebem todos os agentes**, inclusive os cadastrados depois, e podem desmarcar os que não querem usar no chat. Na tela de administração, eles continuam vendo todos.
- **Login com o usuário e a senha da rede** (AD `flexivel.local`). Aceita `nome.sobrenome`, `FLEXIVEL\nome.sobrenome` ou `nome.sobrenome@flexivel.local`. Só entra quem está cadastrado.
- **Registros com filtros**: logins (ok, senha errada, sem liberação, conta desabilitada, bloqueio) e uso dos agentes (perguntas, erros, áudios), filtráveis por usuário, agente, tipo e período.
- **Conversas de todos os usuários**: o administrador lê qualquer conversa de qualquer agente e exporta em TXT a conversa aberta, as selecionadas ou todas as do filtro.
- **Chat com streaming**: seletor de agente na barra lateral (e atalhos na tela inicial), histórico separado por agente, status do que o agente está fazendo (consultando o Qlik, buscando certificados etc.), renomear/apagar, copiar e avaliar (👍/👎 vão para os registros do Dify).
- **Perguntas por áudio**: o botão de microfone grava a pergunta, o Dify transcreve com o modelo de voz para texto do agente e o texto vai para a caixa, para revisar antes de enviar. O áudio não é guardado.
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

## Registros e conversas (administração)

As duas abas ficam em **menu do avatar → Administração**.

### Registros

Junta dois tipos de evento numa lista só, do mais novo para o mais antigo:

| Tipo | Eventos | Onde fica |
|---|---|---|
| **Login** | Entrou, senha inválida, sem acesso liberado, acesso desativado, conta desabilitada no AD, bloqueio por tentativas, erro no AD | IP de quem tentou |
| **Uso dos agentes** | Pergunta ao agente, erro na resposta (com o motivo), áudio transcrito (com o tamanho) | Agente usado e link **Ver conversa** |

Filtros disponíveis:

- **Usuário**: aceita `nome.sobrenome`, `FLEXIVEL
ome.sobrenome` ou `nome.sobrenome@flexivel.local`, com sugestões dos cadastrados.
- **Agente**.
- **Tipo**: logins e uso, só logins ou só uso.
- **Período (De / Até)**: começa nos últimos 7 dias.

Ao escolher um agente, os logins somem da lista, porque o login não pertence a nenhum agente. A tela mostra até 1.000 registros; para ver os anteriores, use um período menor.

O registro de uso guarda **só os metadados** (quem, quando, qual agente e qual conversa). O conteúdo das perguntas e respostas continua só no Dify. O uso começa a ser registrado a partir desta versão; os logins antigos continuam lá.

### Conversas

Lista as conversas de **todos os usuários em todos os agentes**, buscadas direto no Dify, com os mesmos filtros: usuário, agente e período. O período começa nos últimos 30 dias e vale pela última atividade da conversa.

- **Ver**: abre a conversa inteira, só para leitura, com horários, fontes consultadas e avaliações 👍/👎.
- **Exportar TXT**, de três jeitos:
  - **a conversa aberta**: botão *Exportar TXT* no leitor;
  - **as selecionadas**: marque as caixinhas e clique em *Exportar selecionadas*;
  - **todas as do filtro**: *Exportar todas (TXT)*, que leva todas as conversas do filtro, mesmo as que não cabem na tela (a lista mostra as 500 mais recentes).

O arquivo `conversas-AAAAMMDD-HHMM.txt` sai em UTF-8 com quebras de linha do Windows, então abre certinho no Bloco de Notas. Para cada conversa, ele traz:

- um cabeçalho com título, usuário, agente, período, número de mensagens e ID;
- cada pergunta e resposta, com data e hora;
- as fontes consultadas e a avaliação, quando houver.

O limite é de 1.000 conversas por arquivo (`EXPORTAR_MAX_CONVERSAS`); acima disso, filtre por período, usuário ou agente.

Detalhes que valem saber:

- **Auditoria**: abrir e exportar conversas fica registrado na tabela de auditoria (quem, quando e quais conversas).
- **Privacidade (LGPD)**: os usuários devem saber que as conversas podem ser lidas pela administração. Vale incluir isso na política interna de uso.
- **De onde vêm as conversas**: o Dify só lista conversas por usuário, então a plataforma consulta cada combinação de usuário e agente. Entram os usuários cadastrados e também quem já foi removido mas usou algum agente a partir desta versão. Conversas feitas fora desta plataforma (por exemplo, no WebApp do Dify) não aparecem.
- Ao consultar um usuário num agente que ele nunca usou, o Dify cria um registro de usuário final vazio para o app. Isso não aparece nos logs do Dify e não afeta nada.

## Perguntas por áudio

O botão de microfone aparece ao lado do botão de enviar **só nos agentes que aceitam áudio**.

### Como usar

1. Toque no microfone. Na primeira vez, o navegador pede permissão para usar o microfone.
2. Fale a pergunta. O cronômetro mostra o tempo gravado; o limite é de 5 minutos.
3. Toque em **✓** para terminar, ou na lixeira (ou Esc) para descartar.
4. O texto transcrito aparece na caixa. Confira, principalmente códigos de produto e números, corrija se precisar e envie.

O texto vai para a caixa em vez de ser enviado direto porque a transcrição pode errar nomes técnicos (por exemplo, "FLEXX AG 2032"), e uma pergunta errada para o agente gasta tempo e consulta à toa.

### Como ligar o áudio num agente (no Dify)

1. **Modelo de voz para texto no workspace**: em **Configurações → Provedor de modelos**, configure um provedor com modelo *Speech to Text* (por exemplo, OpenAI `whisper-1`) e defina-o como padrão de **Voz para texto** em **Modelos do sistema**.
2. **Recurso no app**: abra o agente no Dify → **Recursos** (ou **Funcionalidades**) → ligue **Voz para texto** (*Speech to Text*) → **Publicar**.
3. Em até 5 minutos, o botão de microfone aparece para quem tem acesso ao agente. Para ver na hora, recarregue a página.

Agentes sem o recurso ligado não mostram o microfone. Se o modelo de voz para texto não estiver configurado ou estiver sem cota, a pessoa vê uma mensagem explicando o problema.

### Como funciona por dentro

- O navegador grava com o formato nativo dele (webm no Chrome/Edge/Firefox, mp4 no Safari) e **converte para WAV 16 kHz mono** antes de enviar, porque o Dify não aceita webm. Isso dá cerca de 1,9 MB por minuto.
- O servidor confere se a pessoa tem acesso ao agente e se o arquivo está dentro do limite (`AUDIO_MAX_MB`, padrão 12 MB), repassa para `/v1/audio-to-text` do Dify com a chave do agente e devolve só o texto. **O áudio não fica guardado** em lugar nenhum desta aplicação.
- A gravação só funciona com **HTTPS** (exigência dos navegadores para usar o microfone).
- O nginx precisa aceitar o tamanho do áudio: `client_max_body_size 12m` (já está em `deploy/nginx-agente.conf`). Com o limite antigo de 1 MB, áudios com mais de uns 30 segundos são recusados e a pessoa vê *"O servidor recusou o áudio por tamanho"*.

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

#### Atualizando para a versão com áudio

Além do `git pull` e do `docker compose up -d --build`, aumente o tamanho máximo de upload no nginx que faz o HTTPS:

- **Opção A, nginx no host**: no arquivo do site (por exemplo, `/etc/nginx/conf.d/nginx-agente.conf`), troque `client_max_body_size 1m;` por `client_max_body_size 12m;` e rode `sudo nginx -t && sudo systemctl reload nginx`.
- **Opção B, nginx do Dify**: faça a mesma troca no bloco `server` do agente em `dify/docker/nginx/conf.d/` e reinicie o nginx do Dify (`docker compose restart nginx` na pasta `dify/docker`).

Depois, ligue **Voz para texto** nos agentes que devem aceitar áudio (veja [Perguntas por áudio](#perguntas-por-áudio)).

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
pip install playwright && python tests/test_ui.py   # interface no Chrome, com microfone simulado (prints em tests/prints/)
```

O Dify falso pagina como o de verdade (100 conversas por página; mensagens das mais novas para as mais antigas) e tem uma rota só de teste (`POST /teste/conversa`) que cria conversas com datas no passado e muitas mensagens. Com ela, os testes conferem a paginação (121 conversas; uma conversa com 250 mensagens) e os filtros por data.

`test_api.py` e `test_ui.py` esperam um servidor recém-iniciado: reinicie o `dev_server.py` entre um e outro.

O Dify falso tem três apps:

- **Agente Técnico PU(qlik)**: agente com ferramentas e voz para texto ligada. A chave dele é a `DIFY_API_KEY` padrão, então vira o primeiro agente na subida. A "transcrição" falsa devolve a duração e o formato do WAV recebido, o que permite conferir a conversão feita no navegador.
- **Agente Comercial**: chatflow sem ferramentas e sem voz para texto, com a chave `app-Comercial0000000000000`.
- **Fluxo de relatórios**: workflow que deve ser recusado no cadastro, com a chave `app-Fluxo00000000000000000`.

Usuários do AD falso, todos com a senha `Teste@123`:

- `admin.ti`: admin inicial.
- `maria.silva` e `carlos.souza`: precisam ser liberados pelo admin.
- `joao.desligado`: conta desabilitada.
