# Agentes do Grupo Flexível · frontend para o Dify

Frontend próprio (PWA) para os agentes de conversa do Dify, com a identidade visual do Grupo Flexível, login pelo Active Directory e controle de **quem usa qual agente**.

- **Vários agentes do Dify numa tela só.** O administrador cadastra cada agente colando a chave de API dele, e a plataforma busca no Dify o nome, a descrição e o ícone. Por exemplo, o *Agente Técnico PU(qlik)* (acesso completo às bases) e um agente com menos acesso para outras áreas.
- **Cada usuário só vê os agentes liberados para ele.** No cadastro, o admin marca os agentes de cada pessoa. A permissão é conferida a cada requisição no servidor, então tirar a marca corta o acesso na hora, até com a tela aberta.
- **Administradores recebem todos os agentes**, inclusive os cadastrados depois, e podem desmarcar os que não querem usar no chat. Na tela de administração, eles continuam vendo todos.
- **Login com o usuário e a senha da rede** (AD `flexivel.local`). Aceita `nome.sobrenome`, `FLEXIVEL\nome.sobrenome` ou `nome.sobrenome@flexivel.local`. Só entra quem está cadastrado.
- **Registros com filtros**: logins (ok, senha errada, sem liberação, conta desabilitada, bloqueio) e uso dos agentes (perguntas, erros, áudios), filtráveis por usuário, agente, tipo e período.
- **Conversas de todos os usuários**: o administrador lê qualquer conversa de qualquer agente e exporta em TXT a conversa aberta, as selecionadas ou todas as do filtro.
- **Chat com streaming**: seletor de agente na barra lateral (e atalhos na tela inicial), histórico separado por agente, status do que o agente está fazendo (consultando o Qlik, buscando certificados etc.), renomear/apagar, copiar e avaliar (👍/👎 vão para os registros do Dify).
- **Envio de conhecimento com aprovação**: o usuário envia um arquivo (PDF, Office, apresentações, planilhas, imagens) para um agente aprender. O arquivo passa por leitura máxima (conversão para PDF e OCR), fica em quarentena **nesta plataforma** (nada chega ao Dify antes de aprovado), recebe alertas automáticos (instruções suspeitas, dados pessoais, pouco texto) e só entra na base do agente depois da aprovação de um aprovador daquele agente. Só administradores desativam ou excluem um conhecimento aprovado.
- **Avaliações viram treinamento**: nos agentes que recebem conhecimento, cada 👍 manda a resposta para aprovação como "resposta validada", e cada 👎 manda para aprovação como "correção". No 👎, a pessoa pode escrever a resposta certa (opcional); se não escrever, o aprovador escreve ao aprovar. Só a resposta correta entra na base do agente.
- **Perguntas por áudio**: o botão de microfone grava a pergunta, o Dify transcreve com o modelo de voz para texto do agente e o texto vai para a caixa, para revisar antes de enviar. O áudio não é guardado.
- **Anexos no chat**: nos agentes com "Upload de Arquivo" ligado no Dify, o clipe anexa imagens e documentos (ou um print colado) para o agente analisar naquela pergunta. O arquivo vai direto para o Dify e não fica guardado aqui.
- **Ouvir a resposta**: nos agentes com "Texto para voz" ligado no Dify, o botão 🔊 lê a resposta com a voz do agente, sem ler tabelas nem símbolos de formatação.
- **Celular**: layout responsivo, instalável como app (PWA). A tela de login mostra um card para instalar (Android: botão *Instalar*; iPhone: instrução *Compartilhar → Adicionar à Tela de Início*).
- **Segurança**: as chaves de API do Dify ficam só no servidor (no banco local) e nunca vão para o navegador. A tela de administração mostra só o começo e o fim da chave. A sessão usa cookie HttpOnly + SameSite=Strict. Há proteção CSRF, CSP restritiva e bloqueio após 5 senhas erradas (10 min).

## Estrutura

```
backend/app/        FastAPI: login LDAP, cadastro de usuários e agentes (SQLite em /data), proxy da API do Dify
  conhecimento.py   envio de conhecimento com aprovação (validação, quarentena, alertas, decisões, registros)
conversor/          container isolado: LibreOffice (Office -> PDF) + OCRmyPDF/Tesseract (OCR português/inglês)
frontend/           PWA (HTML/CSS/JS sem build), ícones e logo
deploy/             exemplo de configuração do nginx (HTTPS + streaming)
tests/              AD/Dify/conversor falsos e testes da API, da migração, da interface e do conversor real
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

- **Usuário**: aceita `nome.sobrenome`, `FLEXIVEL\nome.sobrenome` ou `nome.sobrenome@flexivel.local`, com sugestões dos cadastrados.
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

## Envio de conhecimento com aprovação

Os usuários enviam arquivos para os agentes aprenderem, mas **nada entra na base de um agente sem a aprovação de um aprovador daquele agente**.

### Como funciona

```
Usuário envia arquivo ──► validação (tipo real, tamanho, macros, bomba de compressão)
                     ──► QUARENTENA LOCAL: o arquivo fica no servidor desta plataforma
                     ──► conversor isolado: PDF + OCR quando preciso e extração do texto (máxima extração)
                     ──► alertas automáticos (instruções suspeitas, dados pessoais, pouco texto)
                     ──► aprovador analisa: arquivo, texto extraído (prévia), alertas
                           ├─ Aprovar ──► só agora o arquivo vai para a base do agente no Dify (ex.: "Feedback")
                           └─ Rejeitar (motivo obrigatório) ──► é apagado do servidor; quem enviou vê o motivo
```

**Nada que não foi aprovado chega ao Dify.** Enquanto espera, o envio fica só no servidor da plataforma (no volume de dados), e a prévia mostrada ao aprovador é o texto extraído aqui pelo conversor. Por isso a plataforma precisa de acesso a **uma base só** no Dify: a que recebe o conhecimento aprovado. Depois de aprovado, a plataforma acompanha o processamento no Dify; se o Dify não conseguir ler o arquivo, o envio mostra "Aprovado, mas o Dify não conseguiu processar…" e o admin pode excluí-lo.

### Máxima extração do conteúdo

O Dify lê só texto digital. Para não perder o que está em imagens, o conversor (container `conversor`, com LibreOffice e Tesseract) trata cada tipo de arquivo assim:

| Arquivo | Tratamento |
|---|---|
| PDF digital | OCR só nas partes que são imagem (prints, tabelas escaneadas); o texto digital é mantido |
| PDF escaneado, JPG, PNG | OCR completo (português + inglês) |
| PPT, PPTX, DOC, RTF, ODT | Conversão para PDF + OCR das imagens dos slides/páginas |
| DOCX **com imagens** | Conversão para PDF + OCR (para não perder o texto das figuras) |
| DOCX sem imagens, XLSX, XLS, CSV, TXT, MD | Vai como está para o Dify, que preserva melhor tabelas, linhas e colunas. O conversor só lê o texto para a prévia do aprovador |

A plataforma conta quanto texto foi extraído e avisa o aprovador quando veio pouco.

### Papéis e permissões

| Ação | Quem enviou | Aprovador do agente | Administrador |
|---|---|---|---|
| Enviar arquivo | Quem tem acesso ao agente | ✅ | ✅ |
| Ver os próprios envios e o motivo da rejeição | ✅ | ✅ | ✅ |
| Cancelar enquanto está pendente | ✅ (só o próprio) | — | — |
| Ver a fila e analisar | ❌ | Agentes que aprova | Todos (só vê) |
| Aprovar ou rejeitar | ❌ | Agentes que aprova | Só se for marcado como aprovador |
| Aprovar **o próprio** envio | — | Se o agente permitir autoaprovação (fica marcado "Autoaprovado") | Idem |
| Ver os conhecimentos aprovados | Os próprios | Agentes que aprova | Todos |
| Desativar, reativar ou excluir um aprovado | ❌ | ❌ | ✅ (com motivo) |
| Ver os registros de aprovação | ❌ | Agentes que aprova | Todos |

- **Desativar**: o documento continua na base, mas o agente para de consultá-lo na hora; dá para reativar.
- **Excluir**: apaga o documento da base no Dify; não tem volta.
- Desativar, reativar, excluir e rejeitar exigem **motivo**, que fica no registro.

### Configurar (uma vez)

1. **Chave de conhecimento do Dify**: no Dify, abra **Conhecimento → API** (canto superior direito) → **Chave da API** → **Criar nova chave**. Na plataforma: **Administração → Agentes → Envio de conhecimento pelos usuários → Cadastrar chave**. A plataforma testa a chave antes de salvar; ela fica só no servidor e aparece mascarada.
   - **Recomendado (menor acesso possível): chave restrita às bases que vão receber o conhecimento** (por exemplo, **Feedback** e **Documentos**). Ao criar a chave no Dify, restrinja-a a essas bases. A plataforma aceita a chave e mostra "restrita" no quadro; as bases são cadastradas no passo seguinte.
   - Com a **chave geral do workspace**, cadastrar as bases é opcional: sem nenhuma cadastrada, a plataforma lista todas as bases do Dify nos agentes e pode criar uma nova por agente.
   - Também dá para usar `DIFY_DATASET_API_KEY` no `.env` (só chave geral); a chave cadastrada na tela tem prioridade.
   - Ao trocar por uma chave restrita, a plataforma confere se ela alcança todas as bases cadastradas e as que os agentes usam; se não alcançar alguma, recusa e diz qual.
2. **Cadastrar as bases**: em **Administração → Agentes → Bases de conhecimento**, clique em **"+ Adicionar base de conhecimento"** (como em "+ Adicionar agente") e cole o **ID da base**: abra a base no Dify e copie o código que aparece no endereço, logo depois de `/datasets/` (ex.: `https://dify…/datasets/3f2a8c1e-…/documents`). A plataforma confere no Dify e mostra o nome. Repita para cada base (por exemplo, Feedback e Documentos). A lista mostra o nome, o ID, quantos documentos a base tem e quais agentes usam cada uma; a lixeira tira a base da plataforma (nada é apagado no Dify) e só funciona se nenhum agente estiver usando a base.
3. **Ligar o recebimento no agente**: **Administração → Agentes → editar o agente → "Receber conhecimento dos usuários"** e escolha as bases nos dois campos:
   - **"Base para os arquivos enviados"**: onde entram os arquivos aprovados (por exemplo, **Documentos - PU**). Pode ser uma base que você já criou no Dify ou, só com chave geral, **"Criar uma base nova"** (a plataforma cria **Colaborativo - Nome do agente**).
   - **"Base para os feedbacks do chat"**: onde entram as respostas validadas (👍) e as correções (👎) aprovadas (por exemplo, **Feedback - PU**). Deixando **"A mesma base dos arquivos"**, os feedbacks vão para a base dos arquivos.

   **Como a plataforma decide para onde vai cada envio:** pela origem. Envio feito pela tela **"Enviar conhecimento"** (arquivo) vai para a base de arquivos; envio que nasceu de um 👍 ou 👎 no chat vai para a base de feedbacks. O envio guarda em qual base entrou, e o admin consegue desativar ou excluir lá depois.

   As listas mostram as bases que a chave alcança, com o número de documentos e qual agente já usa cada uma. Uma base pode estar vazia: se ainda não tiver tipo de indexação, o primeiro documento aprovado entra em alta qualidade (com embeddings). Recomendação: **uma base de feedbacks e uma de arquivos por agente** e separadas dos documentos oficiais. Assim, o que é aprovado num agente não aparece em outro, e, se os feedbacks se mostrarem ruins, basta desligar a base de feedbacks do agente no Dify sem perder os documentos.

   Dá para trocar as bases depois: os próximos conhecimentos aprovados entram na nova, e os que já foram aprovados continuam na base em que entraram (e continuam podendo ser desativados ou excluídos pelo admin, desde que a chave alcance essa base).
4. **Ligar as bases ao agente no Dify** (o único passo manual, porque a API do Dify não faz isso): abra o agente no Dify → **Contexto/Conhecimento → Adicionar →** as bases escolhidas (por exemplo, **Documentos - PU** e **Feedback - PU**) → **Publicar**.
5. **Marcar os aprovadores**: **Administração → Usuários → botão "Não aprova"/"Aprova: …"** na linha da pessoa → marque os agentes.
6. **Autoaprovação**: no mesmo diálogo do agente, **"Permitir que o aprovador aprove o próprio envio"** (vem ligada). Desligada, o envio de um aprovador espera outro aprovador do mesmo agente.

### Como usar

- **Enviar** (menu do avatar → **Enviar conhecimento**): escolha o agente, o arquivo e escreva o que é o arquivo e por que o agente deve aprender com ele. A lista **Meus envios** mostra a situação de cada um e se atualiza sozinha enquanto o arquivo é processado.
- **Aprovar** (menu → **Aprovações**, com o número de pendentes): abra o envio, leia os **alertas**, abra o arquivo (o PDF convertido mostra exatamente o que o agente vai ler) e confira os **trechos**. Depois, **Aprovar** (comentário opcional) ou **Rejeitar** (motivo obrigatório).
- **Aprovados**: filtros de agente, quem enviou, situação (ativos, desativados, excluídos, rejeitados) e período. O arquivo aprovado é lido direto do Dify.
- **Registros de aprovação**: filtros de agente, usuário (quem enviou ou quem decidiu), ação e período, com exportação **CSV** (abre no Excel). Exportar também fica na auditoria.

### Avaliações do chat (👍 / 👎) viram conhecimento

Nos agentes com **"Receber conhecimento dos usuários"** ligado, as avaliações das respostas também entram na fila de aprovação, para o agente aprender com elas. Nos outros agentes, o 👍/👎 continua indo só para os registros do Dify, como antes.

| Avaliação | O que vai para a fila | O que entra na base se aprovado |
|---|---|---|
| 👍 Resposta útil | Pergunta e resposta do agente ("👍 Resposta validada") | A pergunta e a resposta validada |
| 👎 com correção | Pergunta, resposta do agente e a resposta certa escrita pela pessoa ("✏️ Correção") | A pergunta e a **resposta correta**; o aprovador pode ajustar o texto antes de aprovar |
| 👎 sem correção | Pergunta e resposta do agente ("✏️ Correção") | A pergunta e a **resposta correta escrita pelo aprovador**, que é obrigatória para aprovar |

Como funciona:

- **No chat:** ao clicar em 👎, abre a caixa **"Quer ajudar com a resposta certa?"**. Escrever é opcional: a avaliação já foi para aprovação. Clicar em **"Agora não"** deixa para o aprovador escrever.
- **Para o aprovador:** a correção mostra a pergunta, a resposta do agente (para comparar) e o campo **"Resposta correta que vai para a base do agente"**, já preenchido quando a pessoa escreveu. Sem texto nesse campo, a correção não pode ser aprovada.
- **Só a resposta certa entra na base.** A resposta errada do agente fica apenas na tela do aprovador; se entrasse na base, o agente poderia voltar a usá-la.
- **A pergunta e a resposta são lidas do próprio Dify**, não do navegador. Assim ninguém consegue mandar para aprovação uma "resposta do agente" que ele não deu: se a mensagem não pertence à conversa informada, nada é criado.
- **Mudou de ideia:** retirar o 👍/👎, ou trocar um pelo outro, cancela o envio anterior se ele ainda não foi decidido. Mandar outra correção para a mesma resposta substitui a anterior. Avaliar de novo a mesma resposta não duplica o envio. Cada avaliação é de uma pessoa sobre uma resposta: duas pessoas avaliando respostas com o mesmo texto geram envios separados.
- **Corrigir também conta como 👎:** mandar uma correção registra a avaliação negativa no Dify, mesmo que o 👎 não tenha sido clicado antes.
- **Resposta correta de verdade:** a correção (da pessoa ou do aprovador) precisa ter pelo menos 10 caracteres visíveis (caracteres invisíveis não contam) e não pode ser igual à resposta que foi avaliada como ruim.
- **Limite:** no máximo 60 avaliações por pessoa por hora viram envios (`CONHECIMENTO_AVALIACOES_POR_HORA`). Acima disso, a avaliação continua registrada no Dify, mas não entra na fila, e a pessoa é avisada.
- **Agente removido:** os envios dele que ainda não foram decididos (arquivos e avaliações) são cancelados e apagados do servidor.
- **Para o aprovador, no 👎 ainda pendente:** o quadro de trechos é a **cópia em quarentena** (local), que contém a resposta errada e serve só para conferência. O que vai para a base é apenas o campo "Resposta correta".
- **Mesma segurança dos arquivos:** o texto passa pela quarentena e pelos alertas automáticos. Uma correção com "ignore as instruções anteriores…" gera o alerta de instrução suspeita. As regras de autoaprovação e de quem decide são as mesmas.
- **Registros:** os envios aparecem com o tipo ("Resposta validada (👍)", "Correção (👎)") na lista, no filtro **Tipo** da aba Aprovados e na coluna **Tipo** do CSV dos registros. Quando o aprovador escreve ou ajusta a resposta correta, isso fica registrado.

### Alertas automáticos

Os alertas **não bloqueiam** nada; servem para o aprovador olhar com atenção:

- **Possíveis instruções para manipular o agente** (*prompt injection*): frases como "ignore as instruções anteriores", "revele o prompt", "você agora é…". Se entrarem na base, o agente pode obedecer. Os trechos aparecem no alerta.
- **Dados pessoais ou sensíveis**: CPF e CNPJ (com dígito verificador conferido), e-mails, telefones e palavras como senha, token, salário. No alerta, os números aparecem mascarados.
- **Pouco texto extraído**: o arquivo é quase todo imagem ou de baixa qualidade.
- **OCR não aplicado**: o texto de imagens pode ter ficado de fora.

### Segurança

- **Quarentena local**: até ser aprovado, o envio fica só no servidor da plataforma; nada chega ao Dify. A chave de conhecimento pode ficar **restrita a uma única base** (a que recebe o aprovado).
- **Conversor isolado**: container próprio, sem as chaves, sem acesso ao banco, sem internet (rede `internal` do Docker), sistema de arquivos só de leitura (`/tmp` em memória), sem privilégios (`cap_drop: ALL`, `no-new-privileges`), usuário sem root e com limite de memória, CPU e processos. Um arquivo malicioso que explore falha do LibreOffice não alcança nada importante.
- **Validação do conteúdo real**: a extensão precisa bater com o conteúdo (PDF, ZIP do Office, OLE do Office antigo, PNG, JPEG, RTF); arquivos com macro e bombas de compressão são recusados. O nome do arquivo é limpo (sem pastas nem caracteres especiais).
- **Arquivos abertos no navegador** vêm com `Content-Security-Policy: sandbox`: mesmo um PDF ou TXT malicioso não roda script nem acessa a sessão.
- **Duplicados**: o mesmo arquivo (mesmo SHA-256) não pode ser enviado de novo para o mesmo agente enquanto estiver pendente ou aprovado.
- **Limite de envios**: 30 por pessoa por hora (`CONHECIMENTO_ENVIOS_POR_HORA`).
- **Arquivos locais só até a decisão**: depois de aprovado, rejeitado ou cancelado, a cópia no servidor é apagada; o registro guarda o SHA-256. O arquivo aprovado continua no Dify.
- **Dois aprovadores ao mesmo tempo**: a troca de situação é atômica; o segundo recebe "outro aprovador acabou de decidir".
- **Reinício do container**: envios que estavam sendo processados são retomados na subida; aprovações interrompidas voltam para a fila.

### Configurações (`.env`)

| Variável | Padrão | Para quê |
|---|---|---|
| `CONHECIMENTO_MAX_MB` | `15` | Tamanho máximo do arquivo. O Dify aceita 15 MB por padrão (`UPLOAD_FILE_SIZE_LIMIT` no Dify); acima disso, aumente lá também e no nginx (`client_max_body_size`). |
| `DIFY_DATASET_API_KEY` | vazio | Alternativa à chave cadastrada na tela. |
| `CONVERSOR_URL` | `http://conversor:8000` (no compose) | Endereço do conversor. Vazio = sem conversão (PPT, DOC e imagens ficam indisponíveis; PDF vai sem OCR). |
| `CONHECIMENTO_ENVIOS_POR_HORA` | `30` | Limite de envios de arquivos por pessoa, por hora. |
| `CONHECIMENTO_AVALIACOES_POR_HORA` | `60` | Limite de avaliações do chat (👍/👎) que viram envios, por pessoa, por hora. |

O conversor aceita `OCR_IDIOMAS` (padrão `por+eng`), `CONVERSOES_SIMULTANEAS` (padrão `2`), `CONVERSOR_TIMEOUT_OFFICE` (180 s) e `CONVERSOR_TIMEOUT_OCR` (900 s).

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

## Anexos no chat

O botão de clipe (📎), à esquerda da caixa de pergunta, aparece **só nos agentes com o recurso "Upload de Arquivo" ligado no Dify**. Com ele, a pessoa anexa arquivos para o agente analisar **naquela pergunta**: por exemplo, uma ficha técnica, um laudo, uma planilha ou a foto de um defeito.

Não confunda com o [envio de conhecimento](#envio-de-conhecimento-com-aprovação): o anexo **não passa por aprovação e não entra em nenhuma base**. Ele vale só para a pergunta em que foi enviado.

### Como usar

1. Clique no clipe e escolha até 3 arquivos (o limite vem do Dify). Também dá para **arrastar** arquivos para a caixa ou **colar um print** (Ctrl+V).
2. Cada arquivo sobe na hora e aparece acima da caixa. Se o arquivo for recusado, ele fica marcado em vermelho com o motivo; tire-o no **×**.
3. Escreva a pergunta e envie. Sem texto, vai *"Analise o arquivo anexado."*.
4. Os nomes dos anexos ficam na pergunta, também ao reabrir a conversa, na tela de conversas da administração e no TXT exportado.

Tipos aceitos: **imagens** (PNG, JPG, WEBP, GIF) e **documentos** (PDF, Word, Excel, PowerPoint, CSV, TXT, MD). Áudio, vídeo, HTML e SVG ficam de fora, mesmo que o app do Dify aceite. Na prática vale o que estiver ligado nos dois lados: se o app aceita só imagens, só imagens aparecem.

### Como ligar num agente (no Dify)

1. Abra o app → **Recursos** → ligue **Upload de Arquivo**. Marque **Imagem** e/ou **Documento** e deixe **Upload local** permitido.
2. **O fluxo precisa usar o arquivo.** Só ligar o recurso não basta, porque o arquivo chega no fluxo como `sys.files`:
   - **Documentos:** em Chatflow, ponha um nó **Extrator de documentos** lendo `sys.files` e leve o texto dele para o agente/LLM (na instrução ou na pergunta). Sem esse nó, o documento é ignorado.
   - **Imagens:** o modelo precisa aceitar imagem, e a opção **Visão** tem que estar ligada no nó do agente/LLM, apontando para `sys.files`.
3. **Publique.** Em até 5 minutos, o clipe aparece para quem tem acesso ao agente (recarregue a página para ver na hora).

### Como funciona por dentro

- O servidor confere se a pessoa tem acesso ao agente, se o tipo é aceito, se o conteúdo bate com a extensão (o mesmo controle do envio de conhecimento: recusa executável disfarçado, macros e bomba de compressão) e o tamanho. O tamanho vale o menor entre `ANEXO_MAX_MB` e o limite do Dify (padrão: 15 MB para documento, 10 MB para imagem).
- O arquivo vai direto para `/v1/files/upload` do Dify com a chave do agente e **não fica guardado nesta aplicação**. A pergunta leva só o id devolvido pelo Dify.
- Só quem anexou pode usar o id, e só no mesmo agente, por até 24 horas. Ids de outra pessoa ou de outro agente são recusados.
- O link do arquivo no Dify nunca vai para o navegador; o histórico mostra só o nome.
- Limite de `ANEXOS_POR_HORA` (padrão 60) anexos por pessoa por hora.
- O registro de uso da pergunta mostra quantos e quais arquivos foram anexados.

| Variável | Padrão | Para que serve |
|---|---|---|
| `ANEXO_MAX_MB` | `15` | Tamanho máximo do anexo (vale o menor entre este e o limite do Dify). Acima de 15, aumente também `client_max_body_size` no nginx. |
| `ANEXOS_POR_HORA` | `60` | Anexos por pessoa por hora. |

## Ouvir a resposta

O botão de alto-falante (🔊), junto de copiar e 👍/👎 em cada resposta, aparece **só nos agentes com o recurso "Texto para voz" ligado no Dify**.

### Como usar

- Toque no 🔊: o botão mostra que está carregando e, quando o áudio começa (em poucos segundos, mesmo numa resposta longa), vira ⏹. Toque de novo para parar.
- Se o Dify não começar a mandar o áudio em 60 segundos, a leitura é cancelada com um aviso.
- Só uma resposta toca por vez. Começar outra, trocar de conversa ou de agente para a leitura atual.
- Se a **Reprodução automática** estiver ligada no Dify, a resposta começa a tocar sozinha quando termina de chegar. Alguns navegadores (principalmente o Safari, no iPhone) bloqueiam som automático; aí é só tocar no botão.

### O que é lido

O servidor limpa a resposta antes de mandar para a voz:

- tira negrito, itálico, títulos, marcadores de lista, citações e links (fica só o texto do link);
- **não lê tabelas**: no lugar de cada tabela, a voz diz *"A tabela está na tela."*;
- não lê blocos de código nem endereços de internet;
- respostas longas são lidas até `VOZ_MAX_CARACTERES` (padrão 4000 caracteres, uns 4 minutos de fala), cortando no fim de uma frase, e a voz avisa *"O restante da resposta está na tela."*.

### Como ligar num agente (no Dify)

1. **Modelo de texto para voz no workspace**: em **Configurações → Provedor de modelos**, configure um provedor com modelo *TTS* (por exemplo, OpenAI `tts-1`) e defina-o como padrão de **Texto para voz** em **Modelos do sistema**.
2. **Recurso no app**: abra o agente → **Recursos** → ligue **Texto para voz**. Em **Línguas**, escolha **Português** e escolha uma voz. Se quiser que toque sozinho, ligue **Reprodução automática**. **Publique.**
3. Em até 5 minutos, o botão aparece (recarregue a página para ver na hora).

Cada leitura gasta créditos do modelo de voz. Por isso há o limite `VOZ_POR_HORA` (padrão 100 leituras por pessoa por hora), e cada leitura aparece nos registros como *Resposta ouvida*.

### Como funciona por dentro

- O player do navegador toca direto de `GET /api/agentes/{agente}/conversas/{conversa}/mensagens/{mensagem}/ouvir`. O servidor confere o acesso ao agente, busca o texto da resposta no Dify (só acha mensagens da própria pessoa), limpa o texto e chama `/v1/text-to-audio` com a chave do agente.
- O Dify gera o áudio frase a frase, e o servidor **repassa cada trecho assim que chega**. Assim o navegador começa a tocar no primeiro trecho, sem esperar a resposta inteira virar áudio (numa resposta longa, isso levaria mais de um minuto).
- O áudio **não fica guardado** (nem em cache). A CSP libera `media-src` só para o próprio site.

| Variável | Padrão | Para que serve |
|---|---|---|
| `VOZ_MAX_CARACTERES` | `4000` | Quanto da resposta é lido; o resto fica só na tela. |
| `VOZ_POR_HORA` | `100` | Leituras por pessoa por hora. |

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

#### Porta publicada (`APP_PORTA`)

Por padrão, o app fica em `127.0.0.1:8090`, acessível só pela própria máquina (o nginx local faz o HTTPS). Se o proxy HTTPS estiver em **outra máquina**, coloque `APP_PORTA=8090` no `.env` para abrir a porta para a rede, e libere no firewall **só o IP do proxy**. Nessa porta o tráfego é HTTP puro, inclusive a senha do login. **Não edite o `docker-compose.yml` no servidor**: a mudança local trava o `git pull` nas próximas atualizações.

#### Atualizando para a versão com envio de conhecimento

1. `cd /opt/agente-pu-web && git pull && docker compose up -d --build`. Esta versão traz um segundo container, `agente-pu-conversor` (LibreOffice + OCR). A primeira construção dele baixa uns 600 MB de pacotes e leva alguns minutos; a imagem fica com cerca de 1 GB. Confira com `docker compose ps`: devem aparecer `agente-pu-web` e `agente-pu-conversor`, os dois `healthy`.
2. **nginx**: troque `client_max_body_size` para `16m` (arquivos de até 15 MB) e recarregue (`sudo nginx -t && sudo systemctl reload nginx`, ou `docker compose restart nginx` na pasta `dify/docker` se for o nginx do Dify).
3. O banco é atualizado sozinho na subida: as tabelas novas são criadas e os agentes ganham as opções de conhecimento, desligadas até você ligar.
4. Siga [Configurar (uma vez)](#configurar-uma-vez): cadastre a chave de conhecimento, ligue o recebimento nos agentes, ligue a base Colaborativo ao agente no Dify e marque os aprovadores.

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

O `dev_server.py` sobe também um **conversor falso** (porta 8098), que devolve um "PDF" de mentira com texto. O **conversor de verdade** (LibreOffice + OCR) tem teste próprio, que roda no Docker com as mesmas restrições de produção. Ele gera na hora uma imagem com texto, um PDF escaneado, um PPTX e um PPT com texto e print, e um DOCX com figura, e confere se o PDF devolvido traz o texto digital **e** o texto das imagens:

```bash
docker build -t agente-pu-conversor:teste conversor
docker run -d --name conversor-teste --read-only --tmpfs /tmp:size=1g --cap-drop ALL \
    --security-opt no-new-privileges --memory 2g --pids-limit 256 -p 127.0.0.1:8097:8000 agente-pu-conversor:teste
pip install python-pptx python-docx pypdfium2 pillow
python tests/test_conversor.py
docker rm -f conversor-teste && docker image rm agente-pu-conversor:teste   # a imagem tem ~1 GB
```

O Dify falso pagina como o de verdade (100 conversas por página; mensagens das mais novas para as mais antigas) e tem uma rota só de teste (`POST /teste/conversa`) que cria conversas com datas no passado e muitas mensagens. Com ela, os testes conferem a paginação (121 conversas; uma conversa com 250 mensagens) e os filtros por data.

`test_api.py` e `test_ui.py` esperam um servidor recém-iniciado: reinicie o `dev_server.py` entre um e outro.

O Dify falso tem três apps:

- **Agente Técnico PU(qlik)**: agente com ferramentas e voz para texto ligada. A chave dele é a `DIFY_API_KEY` padrão, então vira o primeiro agente na subida. A "transcrição" falsa devolve a duração e o formato do WAV recebido, o que permite conferir a conversão feita no navegador.
- **Agente Comercial**: chatflow sem ferramentas e sem voz para texto, com a chave `app-Comercial0000000000000`.
- **Fluxo de relatórios**: workflow que deve ser recusado no cadastro, com a chave `app-Fluxo00000000000000000`.

Ele também imita a API de conhecimento: bases, envio de arquivo com processamento assíncrono, trechos paginados, ativar e desativar, apagar e download. A chave de conhecimento é `dataset-Conhecimento00000000`; a `dataset-Restrita0000000000000` simula uma chave restrita, que a plataforma deve recusar. Um arquivo com `ERRO` no nome simula falha de processamento no Dify.

Usuários do AD falso, todos com a senha `Teste@123`:

- `admin.ti`: admin inicial.
- `maria.silva` e `carlos.souza`: precisam ser liberados pelo admin.
- `joao.desligado`: conta desabilitada.
