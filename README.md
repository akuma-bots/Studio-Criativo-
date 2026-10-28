# Studio Criativo Pro

Bot Discord em Python com comandos slash para economia virtual, XP/níveis, boas-vindas, logs e moderação. O **Upstash Redis é o banco de dados principal**; não é necessário configurar SQLite nem disco persistente no Render.

## Rodar localmente
1. Instale Python 3.10 ou mais recente.
2. Execute `pip install -r requirements.txt`.
3. Copie `.env.example` para `.env` e preencha as três variáveis com seus próprios segredos.
4. No Discord Developer Portal, habilite **Server Members Intent** e **Message Content Intent**.
5. Execute `python main.py`.

## Configurar Upstash
1. Crie um banco Redis em https://console.upstash.com/.
2. Na página do banco, copie a **REST URL** e o **REST token**.
3. Cadastre-os no ambiente de execução como `UPSTASH_REDIS_REST_URL` e `UPSTASH_REDIS_REST_TOKEN`.

## Hospedar no Render
O arquivo `render.yaml` define um **Background Worker** sem disco SQLite e com as três variáveis secretas a preencher.
1. Abra o Dashboard do Render e crie um **Blueprint** a partir deste repositório.
2. Revise o serviço `studio-criativo-pro` e o custo do plano Starter antes de confirmar.
3. Preencha `DISCORD_TOKEN`, `UPSTASH_REDIS_REST_URL` e `UPSTASH_REDIS_REST_TOKEN` nos campos secretos do Render. Nunca cole segredos no GitHub nem em mensagens.
4. Faça o deploy e confira os logs: o bot deve confirmar a conexão com Upstash Redis e depois aparecer online.

## Comandos principais
- `/ajuda`, `/ping`
- `/configurar boas_vindas:#canal logs:#canal`
- `/saldo`, `/diario`, `/trabalhar`, `/pagar`, `/ranking`
- `/nivel`, `/ranking_xp`
- `/ban`, `/kick`, `/timeout`, `/limpar`

A economia usa moedas virtuais, sem pagamentos reais. Configure permissões e teste em um servidor privado antes de usar em produção.

## Observação sobre dados antigos
Esta versão grava os novos dados no Redis. Dados que existam no antigo SQLite não são migrados automaticamente; se houver dados importantes nesse arquivo, faça uma migração antes de abandonar o banco antigo.
