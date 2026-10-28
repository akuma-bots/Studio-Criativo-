# Studio Criativo Pro

Bot Discord em Python com comandos slash para economia virtual, XP/níveis, boas-vindas, logs e moderação.

## Rodar localmente
1. Instale Python 3.10 ou mais recente.
2. Execute `pip install -r requirements.txt`.
3. Copie `.env.example` para `.env` e configure `DISCORD_TOKEN`.
4. No Discord Developer Portal, habilite **Server Members Intent** e **Message Content Intent**.
5. Execute `python main.py`.

## Hospedar no Render
O arquivo `render.yaml` define um **Background Worker** com disco persistente para manter o banco SQLite entre reinícios.
1. Abra o Dashboard do Render e crie um **Blueprint** a partir deste repositório.
2. Revise o serviço `studio-criativo-pro` e o custo do plano Starter antes de confirmar.
3. Quando solicitado, preencha `DISCORD_TOKEN` com o token do bot no campo secreto do Render.
4. Faça o deploy e confira os logs até aparecer a mensagem de bot online.

O bot precisa de um token válido para iniciar. Nunca coloque o token no GitHub, no README ou em mensagens públicas. Se o token vazar, redefina-o no Discord Developer Portal.

## Comandos principais
- `/ajuda`, `/ping`
- `/configurar boas_vindas:#canal logs:#canal`
- `/saldo`, `/diario`, `/trabalhar`, `/pagar`, `/ranking`
- `/nivel`, `/ranking_xp`
- `/ban`, `/kick`, `/timeout`, `/limpar`

A loja/economia usa moedas virtuais, sem pagamentos reais. Configure as permissões e teste em um servidor privado antes de usar em produção.
