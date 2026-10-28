import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from upstash_redis.asyncio import Redis

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").strip()
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Upstash Redis is the primary persistent database. Credentials are environment-only.
# Delay client construction when credentials are absent so the startup error stays readable.
redis = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN) if UPSTASH_URL and UPSTASH_TOKEN else None

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


def user_key(guild_id: int, user_id: int) -> str:
    return f"scpro:user:{guild_id}:{user_id}"


def settings_key(guild_id: int) -> str:
    return f"scpro:settings:{guild_id}"


def ranking_key(guild_id: int, kind: str) -> str:
    return f"scpro:ranking:{kind}:{guild_id}"


async def get_json(key: str, default):
    value = await redis.get(key)
    if value is None:
        return default
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value) if isinstance(value, str) else value


async def put_json(key: str, value) -> None:
    await redis.set(key, json.dumps(value, ensure_ascii=False))


async def get_settings(guild_id: int) -> dict:
    return await get_json(settings_key(guild_id), {})


async def get_user(guild_id: int, user_id: int) -> dict:
    key = user_key(guild_id, user_id)
    user = await get_json(key, None)
    if user is None:
        user = {
            "coins": 0, "xp": 0, "level": 0,
            "last_daily": 0, "last_work": 0
        }
        await put_json(key, user)
        await redis.zadd(ranking_key(guild_id, "coins"), {str(user_id): 0})
        await redis.zadd(ranking_key(guild_id, "xp"), {str(user_id): 0})
    return user


async def save_user(guild_id: int, user_id: int, user: dict) -> None:
    await put_json(user_key(guild_id, user_id), user)
    await redis.zadd(ranking_key(guild_id, "coins"), {str(user_id): int(user["coins"])})
    await redis.zadd(ranking_key(guild_id, "xp"), {str(user_id): int(user["xp"])})


async def log_event(guild: discord.Guild, message: str) -> None:
    """Registra eventos em um embed padronizado do Studio Draconic."""
    settings = await get_settings(guild.id)
    channel_id = settings.get("log_channel") or 1565018509694738502
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        channel = guild.get_channel(1565018509694738502)
    if not isinstance(channel, discord.TextChannel):
        return

    raw = str(message or "Evento sem detalhes")
    lowered = raw.lower()
    if any(word in lowered for word in ("banido", "expulso", "timeout", "moderação", "anti-spam", "anti-raid", "raid", "menções excessivas")):
        title, color, category, icon = "REGISTRO DE MODERAÇÃO", discord.Color.from_rgb(235, 65, 90), "SEGURANÇA", "🛡️"
    elif any(word in lowered for word in ("pedido", "compra", "pagamento", "pago", "entregue", "produto")):
        title, color, category, icon = "REGISTRO DA LOJA", discord.Color.from_rgb(155, 35, 255), "COMERCIAL", "🛍️"
    elif any(word in lowered for word in ("ticket", "atendimento", "feedback")):
        title, color, category, icon = "REGISTRO DE ATENDIMENTO", discord.Color.from_rgb(155, 35, 255), "SUPORTE", "🎫"
    elif any(word in lowered for word in ("entrou", "saiu", "verificação", "verificacao")):
        title, color, category, icon = "ATIVIDADE DA COMUNIDADE", discord.Color.from_rgb(90, 130, 255), "MEMBROS", "👥"
    else:
        title, color, category, icon = "REGISTRO DO SISTEMA", discord.Color.from_rgb(155, 35, 255), "SISTEMA", "🐉"

    clean_message = discord.utils.escape_mentions(raw)
    embed = discord.Embed(
        title=f"{icon}  {title}",
        description=clean_message[:3500],
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Servidor", value=discord.utils.escape_markdown(guild.name)[:200], inline=True)
    embed.add_field(name="Categoria", value=category, inline=True)
    embed.set_footer(text="STUDIO DRACONIC • Central de registros")
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logging.exception("Falha ao enviar log do Studio Draconic")


class StudioBot(commands.Bot):
    async def setup_hook(self):
        try:
            await redis.ping()
            logging.info("Conexão com Upstash Redis confirmada.")
        except Exception:
            logging.exception("Não foi possível conectar ao Upstash Redis.")
            raise
        self.redis = redis
        self.get_settings = get_settings
        self.put_json = put_json
        self.settings_key = settings_key
        self.get_user_data = get_user
        self.save_user_data = save_user
        self.log_event = log_event
        await self.load_extension("features")
        synced = await self.tree.sync()
        logging.info("Sincronizados %s comandos slash", len(synced))


bot = StudioBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logging.info("Online como %s (%s)", bot.user, bot.user.id if bot.user else "?")


@bot.event
async def on_member_join(member: discord.Member):
    settings = await get_settings(member.guild.id)
    channel_id = settings.get("welcome_channel")
    if channel_id:
        channel = member.guild.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel):
            embed = discord.Embed(
                title="Bem-vindo(a) ao Studio Criativo! ✨",
                description=f"Olá {member.mention}! Seja bem-vindo(a) ao **{member.guild.name}**.",
                color=discord.Color.blurple()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)
    await log_event(member.guild, f"📥 {member.mention} entrou no servidor.")


@bot.event
async def on_member_remove(member: discord.Member):
    await log_event(member.guild, f"📤 **{discord.utils.escape_markdown(member.display_name)}** saiu do servidor.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    user = await get_user(message.guild.id, message.author.id)
    now = int(time.time())
    if len(message.content.strip()) >= 3:
        allowed = await redis.set(
            f"scpro:cooldown:xp:{message.guild.id}:{message.author.id}",
            "1", nx=True, ex=60
        )
        if allowed:
            old_level = int(user["level"])
            user["xp"] = int(user["xp"]) + random.randint(8, 15)
            user["level"] = int((user["xp"] / 100) ** 0.5)
            await save_user(message.guild.id, message.author.id, user)
            settings = await get_settings(message.guild.id)
            level_roles = settings.get("level_roles", {})
            eligible = [(int(level), int(role_id)) for level, role_id in level_roles.items() if int(user["level"]) >= int(level)]
            if eligible and isinstance(message.author, discord.Member):
                target_level, target_role_id = max(eligible, key=lambda item: item[0])
                target_role = message.guild.get_role(target_role_id)
                if target_role and target_role not in message.author.roles and message.guild.me and target_role < message.guild.me.top_role:
                    try:
                        await message.author.add_roles(target_role, reason=f"Recompensa por alcançar nível {target_level}")
                    except discord.HTTPException:
                        logging.exception("Falha ao atribuir cargo por nível")
            if user["level"] > old_level:
                level_channel_id = settings.get("level_channel")
                level_channel = message.guild.get_channel(int(level_channel_id)) if level_channel_id else None
                destination = level_channel if isinstance(level_channel, discord.TextChannel) else message.channel
                await destination.send(
                    f"🎉 {message.author.mention} subiu para o nível **{user['level']}**!"
                )
    await bot.process_commands(message)


@bot.tree.command(name="ajuda", description="Mostra os recursos do Studio Criativo Pro")
async def ajuda(interaction: discord.Interaction):
    embed = discord.Embed(title="Studio Criativo Pro • Ajuda", color=discord.Color.blurple())
    embed.description = (
        "**Economia:** /saldo, /diario, /trabalhar, /pagar, /ranking, /loja, /comprar, /inventario\n"
        "**Experiência:** /nivel, /ranking_xp, /missao, /conquistas, /cargo_nivel\n"
        "**Comunidade:** /sugerir, /votacao, /sorteio, /ticket\n"
        "**Loja GTA RP:** /catalogo_roupas, /tutorial_loja, /produto_cadastrar, /configurar_loja_pix\n"
        "**Tickets:** /painel_tickets (publica painel), /ticket\n"
        "**Pedidos Pix:** /pedido_pago, /pedido_entregar (equipe)\n"
        "**Verificação:** /configurar_verificacao (painel por botão)\n"
        "**Configuração:** /configurar, /configurar_sistemas, /autorole\n"
        "**Moderação:** /ban, /kick, /timeout, /limpar\n"
        "**Utilidade:** /ping"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ping", description="Verifica a latência do bot e o banco Redis")
async def ping(interaction: discord.Interaction):
    try:
        await redis.ping()
        status = "Upstash Redis conectado"
    except Exception:
        logging.exception("Falha no ping do Redis")
        status = "⚠️ Redis indisponível"
    await interaction.response.send_message(
        f"🏓 Pong! Latência: {round(bot.latency * 1000)} ms.\nBanco: {status}.",
        ephemeral=True
    )


@bot.tree.command(name="configurar", description="Configura canal de boas-vindas e canal de logs")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(boas_vindas="Canal para mensagens de entrada", logs="Canal para logs")
async def configurar(
    interaction: discord.Interaction,
    boas_vindas: discord.TextChannel = None,
    logs: discord.TextChannel = None
):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
    settings = await get_settings(interaction.guild.id)
    if boas_vindas is not None:
        settings["welcome_channel"] = boas_vindas.id
    if logs is not None:
        settings["log_channel"] = logs.id
    await put_json(settings_key(interaction.guild.id), settings)
    await interaction.response.send_message("✅ Configurações salvas no Upstash Redis.", ephemeral=True)


@bot.tree.command(name="saldo", description="Consulta seu saldo de moedas virtuais")
async def saldo(interaction: discord.Interaction, membro: discord.Member = None):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
    target = membro or interaction.user
    user = await get_user(interaction.guild.id, target.id)
    await interaction.response.send_message(
        f"💰 Saldo de {target.mention}: **{user['coins']} moedas**.", ephemeral=True
    )


@bot.tree.command(name="diario", description="Resgata moedas uma vez a cada 24 horas")
async def diario(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
    gid, uid = interaction.guild.id, interaction.user.id
    allowed = await redis.set(f"scpro:cooldown:daily:{gid}:{uid}", "1", nx=True, ex=86400)
    if not allowed:
        ttl = await redis.ttl(f"scpro:cooldown:daily:{gid}:{uid}")
        remain = max(int(ttl or 0), 0)
        return await interaction.response.send_message(
            f"Você já resgatou. Volte em {remain//3600}h {(remain%3600)//60}min.",
            ephemeral=True
        )
    user = await get_user(gid, uid)
    reward = random.randint(100, 250)
    user["coins"] = int(user["coins"]) + reward
    user["last_daily"] = int(time.time())
    await save_user(gid, uid, user)
    await interaction.response.send_message(
        f"🎁 Você recebeu **{reward} moedas virtuais**!", ephemeral=True
    )


@bot.tree.command(name="trabalhar", description="Ganha moedas virtuais uma vez por hora")
async def trabalhar(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
    gid, uid = interaction.guild.id, interaction.user.id
    cooldown_key = f"scpro:cooldown:work:{gid}:{uid}"
    allowed = await redis.set(cooldown_key, "1", nx=True, ex=3600)
    if not allowed:
        ttl = max(int(await redis.ttl(cooldown_key) or 0), 0)
        return await interaction.response.send_message(
            f"Você já trabalhou recentemente. Tente novamente em {ttl//60} minutos.",
            ephemeral=True
        )
    user = await get_user(gid, uid)
    reward = random.randint(25, 100)
    user["coins"] = int(user["coins"]) + reward
    user["last_work"] = int(time.time())
    await save_user(gid, uid, user)
    await interaction.response.send_message(
        f"🧰 Você ganhou **{reward} moedas virtuais**!", ephemeral=True
    )


@bot.tree.command(name="pagar", description="Transfere moedas virtuais para outro membro")
async def pagar(
    interaction: discord.Interaction,
    membro: discord.Member,
    quantidade: app_commands.Range[int, 1, 1000000]
):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
    gid, sender, receiver = interaction.guild.id, interaction.user.id, membro.id
    if sender == receiver or membro.bot:
        return await interaction.response.send_message(
            "Escolha outro membro que não seja um bot.", ephemeral=True
        )
    payer = await get_user(gid, sender)
    if int(payer["coins"]) < quantidade:
        return await interaction.response.send_message("Saldo insuficiente.", ephemeral=True)
    recipient = await get_user(gid, receiver)
    payer["coins"] = int(payer["coins"]) - quantidade
    recipient["coins"] = int(recipient["coins"]) + quantidade
    await save_user(gid, sender, payer)
    await save_user(gid, receiver, recipient)
    await interaction.response.send_message(
        f"💸 {interaction.user.mention} transferiu {quantidade} moedas para {membro.mention}."
    )


async def send_ranking(interaction: discord.Interaction, kind: str):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
    entries = await redis.zrevrange(ranking_key(interaction.guild.id, kind), 0, 9, withscores=True)
    lines = []
    for i, entry in enumerate(entries, 1):
        user_id, score = entry
        if isinstance(user_id, bytes):
            user_id = user_id.decode("utf-8")
        member = interaction.guild.get_member(int(user_id))
        label = discord.utils.escape_markdown(member.display_name if member else str(user_id))
        if kind == "coins":
            lines.append(f"**{i}.** {label} — {int(score)} moedas")
        else:
            user = await get_user(interaction.guild.id, int(user_id))
            lines.append(f"**{i}.** {label} — nível {user['level']} ({int(score)} XP)")
    await interaction.response.send_message("\n".join(lines) or "Ainda não há dados.", ephemeral=True)


@bot.tree.command(name="ranking", description="Ranking de moedas virtuais do servidor")
async def ranking(interaction: discord.Interaction):
    await send_ranking(interaction, "coins")


@bot.tree.command(name="nivel", description="Consulta nível e XP")
async def nivel(interaction: discord.Interaction, membro: discord.Member = None):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
    target = membro or interaction.user
    user = await get_user(interaction.guild.id, target.id)
    await interaction.response.send_message(
        f"📈 **{target.display_name}** — nível **{user['level']}**, XP total **{user['xp']}**.",
        ephemeral=True
    )


@bot.tree.command(name="ranking_xp", description="Ranking de XP do servidor")
async def ranking_xp(interaction: discord.Interaction):
    await send_ranking(interaction, "xp")


@bot.tree.command(name="ban", description="Bane um membro")
@app_commands.default_permissions(ban_members=True)
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não informado"):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
    if membro.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        return await interaction.response.send_message(
            "Não posso moderar alguém com cargo igual ou superior ao seu.", ephemeral=True
        )
    await membro.ban(reason=f"{motivo} | Moderador: {interaction.user}")
    await interaction.response.send_message(f"🔨 {membro} foi banido. Motivo: {motivo}")
    await log_event(interaction.guild, f"🔨 {membro} banido por {interaction.user.mention}. Motivo: {motivo}")


@bot.tree.command(name="kick", description="Expulsa um membro")
@app_commands.default_permissions(kick_members=True)
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não informado"):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
    if membro.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        return await interaction.response.send_message(
            "Não posso moderar alguém com cargo igual ou superior ao seu.", ephemeral=True
        )
    await membro.kick(reason=f"{motivo} | Moderador: {interaction.user}")
    await interaction.response.send_message(f"👢 {membro} foi expulso. Motivo: {motivo}")
    await log_event(interaction.guild, f"👢 {membro} expulso por {interaction.user.mention}. Motivo: {motivo}")


@bot.tree.command(name="timeout", description="Aplica timeout em um membro")
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(
    interaction: discord.Interaction,
    membro: discord.Member,
    minutos: app_commands.Range[int, 1, 40320],
    motivo: str = "Não informado"
):
    if interaction.guild is None:
        return await interaction.response.send_message("Use este comando em um servidor.", ephemeral=True)
    if membro.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        return await interaction.response.send_message(
            "Não posso moderar alguém com cargo igual ou superior ao seu.", ephemeral=True
        )
    await membro.timeout(
        datetime.now(timezone.utc) + timedelta(minutes=minutos),
        reason=f"{motivo} | Moderador: {interaction.user}"
    )
    await interaction.response.send_message(
        f"⏳ Timeout aplicado em {membro.mention} por {minutos} minuto(s)."
    )
    await log_event(
        interaction.guild,
        f"⏳ Timeout de {minutos} min em {membro.mention} por {interaction.user.mention}. Motivo: {motivo}"
    )


@bot.tree.command(name="limpar", description="Apaga mensagens recentes")
@app_commands.default_permissions(manage_messages=True)
@app_commands.checks.has_permissions(manage_messages=True)
async def limpar(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 100]):
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("Use em um canal de texto.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=quantidade)
    await interaction.followup.send(f"🧹 {len(deleted)} mensagens apagadas.", ephemeral=True)
    if interaction.guild:
        await log_event(
            interaction.guild,
            f"🧹 {interaction.user.mention} apagou {len(deleted)} mensagens em {interaction.channel.mention}."
        )


async def health_check(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "Studio Criativo Pro"})


async def start_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info("Servidor de saúde HTTP iniciado na porta %s", port)
    return runner


async def main() -> None:
    if not TOKEN:
        raise SystemExit("Configure DISCORD_TOKEN no ambiente antes de iniciar.")
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        raise SystemExit("Configure UPSTASH_REDIS_REST_URL e UPSTASH_REDIS_REST_TOKEN no ambiente.")
    runner = await start_health_server()
    try:
        await bot.start(TOKEN)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
