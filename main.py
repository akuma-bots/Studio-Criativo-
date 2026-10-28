import logging
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_FILE = os.getenv("DATABASE_PATH", "studio_criativo.db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
db = sqlite3.connect(DB_FILE)
db.row_factory = sqlite3.Row


def init_db():
    with db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
          guild_id INTEGER PRIMARY KEY, welcome_channel INTEGER, log_channel INTEGER
        );
        CREATE TABLE IF NOT EXISTS users (
          guild_id INTEGER, user_id INTEGER, coins INTEGER NOT NULL DEFAULT 0,
          xp INTEGER NOT NULL DEFAULT 0, level INTEGER NOT NULL DEFAULT 0,
          last_daily INTEGER NOT NULL DEFAULT 0, last_work INTEGER NOT NULL DEFAULT 0,
          last_xp INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild_id,user_id)
        );
        """)


def ensure_user(guild_id, user_id):
    with db:
        db.execute("INSERT OR IGNORE INTO users(guild_id,user_id) VALUES(?,?)", (guild_id, user_id))


async def log_event(guild, message):
    row = db.execute("SELECT log_channel FROM settings WHERE guild_id=?", (guild.id,)).fetchone()
    if row and row["log_channel"]:
        channel = guild.get_channel(row["log_channel"])
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(message[:1900])
            except discord.HTTPException:
                logging.exception("Falha ao enviar log")


@bot.event
async def setup_hook():
    init_db()
    synced = await bot.tree.sync()
    logging.info("Sincronizados %s comandos slash", len(synced))


@bot.event
async def on_ready():
    logging.info("Online como %s (%s)", bot.user, bot.user.id if bot.user else "?")


@bot.event
async def on_member_join(member):
    row = db.execute("SELECT welcome_channel FROM settings WHERE guild_id=?", (member.guild.id,)).fetchone()
    if row and row["welcome_channel"]:
        channel = member.guild.get_channel(row["welcome_channel"])
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
async def on_member_remove(member):
    await log_event(member.guild, f"📤 **{discord.utils.escape_markdown(member.display_name)}** saiu do servidor.")


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    ensure_user(message.guild.id, message.author.id)
    now = int(time.time())
    row = db.execute("SELECT xp,level,last_xp FROM users WHERE guild_id=? AND user_id=?",
                     (message.guild.id, message.author.id)).fetchone()
    if now - row["last_xp"] >= 60 and len(message.content.strip()) >= 3:
        xp = row["xp"] + random.randint(8, 15)
        level = int((xp / 100) ** 0.5)
        with db:
            db.execute("UPDATE users SET xp=?,level=?,last_xp=? WHERE guild_id=? AND user_id=?",
                       (xp, level, now, message.guild.id, message.author.id))
        if level > row["level"]:
            await message.channel.send(f"🎉 {message.author.mention} subiu para o nível **{level}**!")
    await bot.process_commands(message)


@bot.tree.command(name="ajuda", description="Mostra os recursos do Studio Criativo Pro")
async def ajuda(interaction: discord.Interaction):
    embed = discord.Embed(title="Studio Criativo Pro • Ajuda", color=discord.Color.blurple())
    embed.description = (
        "**Economia:** /saldo, /diario, /trabalhar, /pagar, /ranking\n"
        "**Experiência:** /nivel, /ranking_xp\n"
        "**Configuração:** /configurar\n"
        "**Moderação:** /ban, /kick, /timeout, /limpar\n"
        "**Utilidade:** /ping"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ping", description="Verifica a latência do bot")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! Latência: {round(bot.latency * 1000)} ms.", ephemeral=True)


@bot.tree.command(name="configurar", description="Configura canal de boas-vindas e canal de logs")
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(boas_vindas="Canal para mensagens de entrada", logs="Canal para logs")
async def configurar(interaction: discord.Interaction, boas_vindas: discord.TextChannel = None, logs: discord.TextChannel = None):
    with db:
        db.execute("INSERT OR IGNORE INTO settings(guild_id) VALUES(?)", (interaction.guild.id,))
        if boas_vindas is not None:
            db.execute("UPDATE settings SET welcome_channel=? WHERE guild_id=?", (boas_vindas.id, interaction.guild.id))
        if logs is not None:
            db.execute("UPDATE settings SET log_channel=? WHERE guild_id=?", (logs.id, interaction.guild.id))
    await interaction.response.send_message("✅ Configurações salvas.", ephemeral=True)


@bot.tree.command(name="saldo", description="Consulta seu saldo de moedas virtuais")
async def saldo(interaction: discord.Interaction, membro: discord.Member = None):
    target = membro or interaction.user
    ensure_user(interaction.guild.id, target.id)
    row = db.execute("SELECT coins FROM users WHERE guild_id=? AND user_id=?", (interaction.guild.id, target.id)).fetchone()
    await interaction.response.send_message(f"💰 Saldo de {target.mention}: **{row['coins']} moedas**.", ephemeral=True)


@bot.tree.command(name="diario", description="Resgata moedas uma vez a cada 24 horas")
async def diario(interaction: discord.Interaction):
    gid, uid, now = interaction.guild.id, interaction.user.id, int(time.time())
    ensure_user(gid, uid)
    row = db.execute("SELECT last_daily FROM users WHERE guild_id=? AND user_id=?", (gid, uid)).fetchone()
    if now - row["last_daily"] < 86400:
        remain = 86400 - (now - row["last_daily"])
        return await interaction.response.send_message(f"Você já resgatou. Volte em {remain//3600}h {(remain%3600)//60}min.", ephemeral=True)
    reward = random.randint(100, 250)
    with db:
        db.execute("UPDATE users SET coins=coins+?,last_daily=? WHERE guild_id=? AND user_id=?", (reward, now, gid, uid))
    await interaction.response.send_message(f"🎁 Você recebeu **{reward} moedas virtuais**!", ephemeral=True)


@bot.tree.command(name="trabalhar", description="Ganha moedas virtuais uma vez por hora")
async def trabalhar(interaction: discord.Interaction):
    gid, uid, now = interaction.guild.id, interaction.user.id, int(time.time())
    ensure_user(gid, uid)
    row = db.execute("SELECT last_work FROM users WHERE guild_id=? AND user_id=?", (gid, uid)).fetchone()
    if now - row["last_work"] < 3600:
        return await interaction.response.send_message("Você já trabalhou recentemente. Tente novamente após 1 hora.", ephemeral=True)
    reward = random.randint(25, 100)
    with db:
        db.execute("UPDATE users SET coins=coins+?,last_work=? WHERE guild_id=? AND user_id=?", (reward, now, gid, uid))
    await interaction.response.send_message(f"🧰 Você ganhou **{reward} moedas virtuais**!", ephemeral=True)


@bot.tree.command(name="pagar", description="Transfere moedas virtuais para outro membro")
async def pagar(interaction: discord.Interaction, membro: discord.Member, quantidade: app_commands.Range[int, 1, 1000000]):
    gid, sender, receiver = interaction.guild.id, interaction.user.id, membro.id
    if sender == receiver or membro.bot:
        return await interaction.response.send_message("Escolha outro membro que não seja um bot.", ephemeral=True)
    ensure_user(gid, sender)
    ensure_user(gid, receiver)
    with db:
        row = db.execute("SELECT coins FROM users WHERE guild_id=? AND user_id=?", (gid, sender)).fetchone()
        if row["coins"] < quantidade:
            return await interaction.response.send_message("Saldo insuficiente.", ephemeral=True)
        db.execute("UPDATE users SET coins=coins-? WHERE guild_id=? AND user_id=?", (quantidade, gid, sender))
        db.execute("UPDATE users SET coins=coins+? WHERE guild_id=? AND user_id=?", (quantidade, gid, receiver))
    await interaction.response.send_message(f"💸 {interaction.user.mention} transferiu {quantidade} moedas para {membro.mention}.")


@bot.tree.command(name="ranking", description="Ranking de moedas virtuais do servidor")
async def ranking(interaction: discord.Interaction):
    rows = db.execute("SELECT user_id,coins FROM users WHERE guild_id=? ORDER BY coins DESC LIMIT 10", (interaction.guild.id,)).fetchall()
    lines = []
    for i, row in enumerate(rows, 1):
        member = interaction.guild.get_member(row["user_id"])
        label = discord.utils.escape_markdown(member.display_name if member else str(row["user_id"]))
        lines.append(f"**{i}.** {label} — {row['coins']} moedas")
    await interaction.response.send_message("\n".join(lines) or "Ainda não há dados.", ephemeral=True)


@bot.tree.command(name="nivel", description="Consulta nível e XP")
async def nivel(interaction: discord.Interaction, membro: discord.Member = None):
    target = membro or interaction.user
    ensure_user(interaction.guild.id, target.id)
    row = db.execute("SELECT xp,level FROM users WHERE guild_id=? AND user_id=?", (interaction.guild.id, target.id)).fetchone()
    await interaction.response.send_message(f"📈 **{target.display_name}** — nível **{row['level']}**, XP total **{row['xp']}**.", ephemeral=True)


@bot.tree.command(name="ranking_xp", description="Ranking de XP do servidor")
async def ranking_xp(interaction: discord.Interaction):
    rows = db.execute("SELECT user_id,xp,level FROM users WHERE guild_id=? ORDER BY xp DESC LIMIT 10", (interaction.guild.id,)).fetchall()
    lines = []
    for i, row in enumerate(rows, 1):
        member = interaction.guild.get_member(row["user_id"])
        label = discord.utils.escape_markdown(member.display_name if member else str(row["user_id"]))
        lines.append(f"**{i}.** {label} — nível {row['level']} ({row['xp']} XP)")
    await interaction.response.send_message("\n".join(lines) or "Ainda não há dados.", ephemeral=True)


@bot.tree.command(name="ban", description="Bane um membro")
@app_commands.default_permissions(ban_members=True)
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não informado"):
    if membro.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        return await interaction.response.send_message("Não posso moderar alguém com cargo igual ou superior ao seu.", ephemeral=True)
    await membro.ban(reason=f"{motivo} | Moderador: {interaction.user}")
    await interaction.response.send_message(f"🔨 {membro} foi banido. Motivo: {motivo}")
    await log_event(interaction.guild, f"🔨 {membro} banido por {interaction.user.mention}. Motivo: {motivo}")


@bot.tree.command(name="kick", description="Expulsa um membro")
@app_commands.default_permissions(kick_members=True)
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não informado"):
    if membro.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        return await interaction.response.send_message("Não posso moderar alguém com cargo igual ou superior ao seu.", ephemeral=True)
    await membro.kick(reason=f"{motivo} | Moderador: {interaction.user}")
    await interaction.response.send_message(f"👢 {membro} foi expulso. Motivo: {motivo}")
    await log_event(interaction.guild, f"👢 {membro} expulso por {interaction.user.mention}. Motivo: {motivo}")


@bot.tree.command(name="timeout", description="Aplica timeout em um membro")
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, membro: discord.Member, minutos: app_commands.Range[int, 1, 40320], motivo: str = "Não informado"):
    if membro.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        return await interaction.response.send_message("Não posso moderar alguém com cargo igual ou superior ao seu.", ephemeral=True)
    await membro.timeout(datetime.now(timezone.utc) + timedelta(minutes=minutos), reason=f"{motivo} | Moderador: {interaction.user}")
    await interaction.response.send_message(f"⏳ Timeout aplicado em {membro.mention} por {minutos} minuto(s).")
    await log_event(interaction.guild, f"⏳ Timeout de {minutos} min em {membro.mention} por {interaction.user.mention}. Motivo: {motivo}")


@bot.tree.command(name="limpar", description="Apaga mensagens recentes")
@app_commands.default_permissions(manage_messages=True)
@app_commands.checks.has_permissions(manage_messages=True)
async def limpar(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 100]):
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("Use em um canal de texto.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=quantidade)
    await interaction.followup.send(f"🧹 {len(deleted)} mensagens apagadas.", ephemeral=True)
    await log_event(interaction.guild, f"🧹 {interaction.user.mention} apagou {len(deleted)} mensagens em {interaction.channel.mention}.")


if __name__ == "__main__":
    init_db()
    if not TOKEN:
        raise SystemExit("Configure DISCORD_TOKEN no ambiente antes de iniciar.")
    bot.run(TOKEN)
