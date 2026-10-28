import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger(__name__)
ITEMS = {
    "caixa": {"name": "Caixa Surpresa", "price": 250, "description": "Abra para ganhar 100–500 moedas."},
    "amulet": {"name": "Amuleto da Sorte", "price": 500, "description": "Item colecionável."},
    "trofeu": {"name": "Troféu de Ouro", "price": 1000, "description": "Item colecionável."},
}


def key(guild_id: int, suffix: str) -> str:
    return f"scpro:feature:{guild_id}:{suffix}"


async def get_data(redis, redis_key, default):
    value = await redis.get(redis_key)
    if value is None:
        return default
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value) if isinstance(value, str) else value


async def set_data(redis, redis_key, value):
    await redis.set(redis_key, json.dumps(value, ensure_ascii=False))


async def ephemeral(interaction, content):
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)


async def feature_log(bot, guild, message):
    try:
        await bot.log_event(guild, message)
    except Exception:
        log.exception("Falha ao registrar evento")


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fechar ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="scpro:ticket:close")
    async def close_ticket(self, interaction, button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not channel.topic or "scpro-ticket-owner:" not in channel.topic:
            return await ephemeral(interaction, "Este canal não parece ser um ticket.")
        try:
            owner_id = int(channel.topic.split("scpro-ticket-owner:", 1)[1].split()[0])
        except (ValueError, IndexError):
            owner_id = 0
        member = interaction.user
        allowed = member.id == owner_id or (isinstance(member, discord.Member) and member.guild_permissions.manage_channels)
        if not allowed:
            return await ephemeral(interaction, "Só quem abriu o ticket ou a equipe pode fechá-lo.")
        await interaction.response.send_message("Ticket será fechado em 3 segundos.")
        await asyncio.sleep(3)
        try:
            await channel.delete(reason=f"Ticket fechado por {interaction.user}")
        except discord.HTTPException:
            log.exception("Falha ao excluir ticket")


class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Participar", style=discord.ButtonStyle.success, emoji="🎉", custom_id="scpro:giveaway:join")
    async def join(self, interaction, button):
        if interaction.guild is None or interaction.message is None:
            return await ephemeral(interaction, "Use este botão no servidor.")
        giveaway_key = key(interaction.guild.id, f"giveaway:{interaction.message.id}")
        data = await get_data(interaction.client.redis, giveaway_key, None)
        if not data or data.get("ended"):
            return await ephemeral(interaction, "Este sorteio já terminou.")
        entrants = data.setdefault("entrants", [])
        uid = str(interaction.user.id)
        if uid in entrants:
            return await ephemeral(interaction, "Você já está participando!")
        entrants.append(uid)
        await set_data(interaction.client.redis, giveaway_key, data)
        await ephemeral(interaction, "Você está participando do sorteio!")


class StudioFeatures(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.redis = bot.redis
        self.giveaway_worker.start()

    def cog_unload(self):
        self.giveaway_worker.cancel()

    async def channel_for(self, guild, setting):
        settings = await self.bot.get_settings(guild.id)
        channel_id = settings.get(setting)
        return guild.get_channel(int(channel_id)) if channel_id else None

    @app_commands.command(name="configurar_sistemas", description="Salva os canais padrão dos sistemas")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def configurar_sistemas(
        self, interaction: discord.Interaction,
        boas_vindas: discord.TextChannel = None,
        logs: discord.TextChannel = None,
        tickets: discord.CategoryChannel = None,
        sugestoes: discord.TextChannel = None,
        votacoes: discord.TextChannel = None,
        sorteios: discord.TextChannel = None,
        economia: discord.TextChannel = None,
        niveis: discord.TextChannel = None,
        equipe_ticket: discord.Role = None,
    ):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        settings = await self.bot.get_settings(interaction.guild.id)
        values = {
            "welcome_channel": boas_vindas, "log_channel": logs,
            "ticket_category": tickets, "suggestion_channel": sugestoes,
            "poll_channel": votacoes, "giveaway_channel": sorteios, "economy_channel": economia,
            "level_channel": niveis, "ticket_staff_role": equipe_ticket,
        }
        changed = []
        for name, channel in values.items():
            if channel is not None:
                settings[name] = channel.id
                changed.append(f"• {name}: {channel.mention if isinstance(channel, discord.TextChannel) else channel.name}")
        if not changed:
            return await ephemeral(interaction, "Informe pelo menos um canal ou categoria. Os outros valores serão mantidos.")
        await self.bot.put_json(self.bot.settings_key(interaction.guild.id), settings)
        await interaction.response.send_message("Canais padrão salvos no Upstash Redis:\n" + "\n".join(changed), ephemeral=True)

    @app_commands.command(name="loja", description="Mostra os itens disponíveis")
    async def loja(self, interaction):
        embed = discord.Embed(title="🛒 Loja do Studio Criativo", color=discord.Color.gold())
        for item_id, item in ITEMS.items():
            embed.add_field(name=f"{item['name']} — {item['price']} moedas", value=f"{item_id}: {item['description']}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="comprar", description="Compra um item com moedas")
    @app_commands.choices(item=[
        app_commands.Choice(name="Caixa Surpresa — 250 moedas", value="caixa"),
        app_commands.Choice(name="Amuleto da Sorte — 500 moedas", value="amulet"),
        app_commands.Choice(name="Troféu de Ouro — 1000 moedas", value="trofeu"),
    ])
    async def comprar(self, interaction, item: app_commands.Choice[str], quantidade: app_commands.Range[int, 1, 20] = 1):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        user = await self.bot.get_user_data(interaction.guild.id, interaction.user.id)
        cost = ITEMS[item.value]["price"] * quantidade
        if int(user.get("coins", 0)) < cost:
            return await ephemeral(interaction, f"Saldo insuficiente. Faltam {cost - int(user.get('coins', 0))} moedas.")
        user["coins"] = int(user.get("coins", 0)) - cost
        await self.bot.save_user_data(interaction.guild.id, interaction.user.id, user)
        inv_key = key(interaction.guild.id, f"inventory:{interaction.user.id}")
        inventory = await get_data(self.redis, inv_key, {})
        inventory[item.value] = int(inventory.get(item.value, 0)) + quantidade
        await set_data(self.redis, inv_key, inventory)
        await interaction.response.send_message(f"Comprado: {quantidade}× {ITEMS[item.value]['name']} por {cost} moedas.", ephemeral=True)

    @app_commands.command(name="inventario", description="Mostra os itens que você possui")
    async def inventario(self, interaction, membro: discord.Member = None):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        target = membro or interaction.user
        inventory = await get_data(self.redis, key(interaction.guild.id, f"inventory:{target.id}"), {})
        lines = [f"• {ITEMS[item]['name']} × {count}" for item, count in inventory.items() if item in ITEMS and count > 0]
        await interaction.response.send_message(f"Inventário de {target.display_name}\n" + ("\n".join(lines) if lines else "Ainda está vazio."), ephemeral=True)

    @app_commands.command(name="usar_item", description="Abre uma Caixa Surpresa do inventário")
    async def usar_item(self, interaction):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        inv_key = key(interaction.guild.id, f"inventory:{interaction.user.id}")
        inventory = await get_data(self.redis, inv_key, {})
        if int(inventory.get("caixa", 0)) < 1:
            return await ephemeral(interaction, "Você não tem Caixa Surpresa. Confira /loja.")
        inventory["caixa"] -= 1
        await set_data(self.redis, inv_key, inventory)
        user = await self.bot.get_user_data(interaction.guild.id, interaction.user.id)
        reward = random.randint(100, 500)
        user["coins"] = int(user.get("coins", 0)) + reward
        await self.bot.save_user_data(interaction.guild.id, interaction.user.id, user)
        await interaction.response.send_message(f"Você abriu a caixa e ganhou {reward} moedas!", ephemeral=True)

    @app_commands.command(name="missao", description="Veja ou resgate sua missão diária")
    async def missao(self, interaction):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        gid, uid = interaction.guild.id, interaction.user.id
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = int(await self.redis.get(key(gid, f"mission-count:{day}:{uid}")) or 0)
        claim_key = key(gid, f"mission-claimed:{day}:{uid}")
        claimed = await self.redis.get(claim_key)
        if count >= 20 and not claimed:
            await self.redis.set(claim_key, "1", ex=172800)
            user = await self.bot.get_user_data(gid, uid)
            user["coins"] = int(user.get("coins", 0)) + 300
            user["xp"] = int(user.get("xp", 0)) + 50
            user["level"] = int((user["xp"] / 100) ** 0.5)
            await self.bot.save_user_data(gid, uid, user)
            return await interaction.response.send_message("Missão concluída! +300 moedas e +50 XP.", ephemeral=True)
        status = "Recompensa já resgatada hoje." if claimed else f"Progresso: {min(count, 20)}/20 mensagens válidas. Recompensa: 300 moedas + 50 XP."
        await interaction.response.send_message("Missão diária: envie 20 mensagens válidas.\n" + status, ephemeral=True)

    @app_commands.command(name="conquistas", description="Consulta suas conquistas")
    async def conquistas(self, interaction, membro: discord.Member = None):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        target = membro or interaction.user
        user = await self.bot.get_user_data(interaction.guild.id, target.id)
        coins, xp, level = int(user.get("coins", 0)), int(user.get("xp", 0)), int(user.get("level", 0))
        achievements = [
            ("Primeiro passo", xp >= 100, "Acumule 100 XP"),
            ("Em ascensão", level >= 5, "Alcance o nível 5"),
            ("Poupador", coins >= 1000, "Tenha 1.000 moedas"),
            ("Rico", coins >= 10000, "Tenha 10.000 moedas"),
        ]
        lines = [f"{'✅' if done else '🔒'} {name} — {desc}" for name, done, desc in achievements]
        await interaction.response.send_message(f"Conquistas de {target.display_name}\n" + "\n".join(lines), ephemeral=True)

    @app_commands.command(name="autorole", description="Define o cargo automático para novos membros")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def autorole(self, interaction, cargo: discord.Role = None):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        settings = await self.bot.get_settings(interaction.guild.id)
        if cargo is None:
            settings.pop("autorole_id", None)
            await self.bot.put_json(self.bot.settings_key(interaction.guild.id), settings)
            return await interaction.response.send_message("Cargo automático desativado.", ephemeral=True)
        me = interaction.guild.me
        if cargo.is_default() or cargo.managed or me is None or cargo >= me.top_role:
            return await ephemeral(interaction, "Escolha um cargo abaixo do cargo mais alto do bot.")
        settings["autorole_id"] = cargo.id
        await self.bot.put_json(self.bot.settings_key(interaction.guild.id), settings)
        await interaction.response.send_message(f"Novos membros receberão {cargo.mention}.", ephemeral=True)

    @app_commands.command(name="cargo_nivel", description="Configura um cargo como recompensa de nível")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def cargo_nivel(self, interaction, nivel: app_commands.Range[int, 1, 100], cargo: discord.Role):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        me = interaction.guild.me
        if cargo.is_default() or cargo.managed or me is None or cargo >= me.top_role:
            return await ephemeral(interaction, "O cargo precisa estar abaixo do cargo mais alto do bot.")
        settings = await self.bot.get_settings(interaction.guild.id)
        settings.setdefault("level_roles", {})[str(nivel)] = cargo.id
        await self.bot.put_json(self.bot.settings_key(interaction.guild.id), settings)
        await interaction.response.send_message(f"Cargo {cargo.mention} configurado para o nível {nivel}.", ephemeral=True)

    @app_commands.command(name="ticket", description="Abre um canal privado de suporte")
    async def ticket(self, interaction, assunto: str = "Suporte"):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        guild = interaction.guild
        existing = discord.utils.find(lambda ch: isinstance(ch, discord.TextChannel) and ch.topic and f"scpro-ticket-owner:{interaction.user.id}" in ch.topic, guild.text_channels)
        if existing:
            return await ephemeral(interaction, f"Você já tem um ticket aberto: {existing.mention}")
        settings = await self.bot.get_settings(guild.id)
        category = guild.get_channel(int(settings["ticket_category"])) if settings.get("ticket_category") else None
        if not isinstance(category, discord.CategoryChannel):
            category = None
        me = guild.me
        if me is None:
            return await ephemeral(interaction, "Não consegui verificar as permissões do bot.")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        }
        staff_role = guild.get_role(int(settings["ticket_staff_role"])) if settings.get("ticket_staff_role") else None
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name[:12]}".lower().replace(" ", "-"),
            category=category, overwrites=overwrites,
            topic=f"Ticket: {discord.utils.escape_markdown(assunto)} | scpro-ticket-owner:{interaction.user.id}",
            reason=f"Ticket aberto por {interaction.user}"
        )
        embed = discord.Embed(title="Ticket de suporte", description=f"Olá {interaction.user.mention}! A equipe atenderá você aqui.\nAssunto: {discord.utils.escape_markdown(assunto)}", color=discord.Color.blurple())
        await channel.send(content=interaction.user.mention, embed=embed, view=TicketView())
        await interaction.response.send_message(f"Seu ticket foi criado: {channel.mention}", ephemeral=True)
        await feature_log(self.bot, guild, f"Ticket aberto por {interaction.user.mention}: {channel.mention}")

    @app_commands.command(name="sugerir", description="Envia uma sugestão para a comunidade")
    async def sugerir(self, interaction, texto: str):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        channel = await self.channel_for(interaction.guild, "suggestion_channel")
        if not isinstance(channel, discord.TextChannel):
            return await ephemeral(interaction, "Configure o canal com /configurar_sistemas primeiro.")
        embed = discord.Embed(title="Nova sugestão", description=discord.utils.escape_mentions(texto), color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Autor ID: {interaction.user.id}")
        message = await channel.send(embed=embed)
        for emoji in ("👍", "👎"):
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                pass
        await interaction.response.send_message(f"Sugestão enviada em {channel.mention}.", ephemeral=True)

    @app_commands.command(name="votacao", description="Cria uma votação com até 5 opções")
    async def votacao(self, interaction, pergunta: str, opcoes: str):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        choices = [discord.utils.escape_mentions(x.strip()) for x in opcoes.split("|") if x.strip()]
        if not 2 <= len(choices) <= 5:
            return await ephemeral(interaction, "Informe de 2 a 5 opções separadas por |.")
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
        embed = discord.Embed(title=discord.utils.escape_mentions(pergunta), description="\n".join(f"{emojis[i]} {choice}" for i, choice in enumerate(choices)), color=discord.Color.blurple())
        embed.set_footer(text=f"Votação criada por {interaction.user.display_name}")
        channel = await self.channel_for(interaction.guild, "poll_channel")
        if not isinstance(channel, discord.TextChannel):
            channel = interaction.channel
        await interaction.response.send_message(f"Votação criada em {channel.mention}!", ephemeral=True)
        message = await channel.send(embed=embed)
        for emoji in emojis[:len(choices)]:
            await message.add_reaction(emoji)

    @app_commands.command(name="sorteio", description="Cria um sorteio com participação por botão")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sorteio(self, interaction, premio: str, minutos: app_commands.Range[int, 1, 10080], ganhadores: app_commands.Range[int, 1, 10] = 1):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando em um servidor.")
        channel = await self.channel_for(interaction.guild, "giveaway_channel")
        if not isinstance(channel, discord.TextChannel):
            channel = interaction.channel
        end_at = int(time.time()) + minutos * 60
        embed = discord.Embed(title="SORTEIO", description=f"Prêmio: {discord.utils.escape_mentions(premio)}\nGanhadores: {ganhadores}\nEncerra: <t:{end_at}:R>\n\nClique no botão para participar!", color=discord.Color.gold())
        embed.set_footer(text=f"Criado por {interaction.user.display_name}")
        await interaction.response.send_message(f"Sorteio criado em {channel.mention}.", ephemeral=True)
        message = await channel.send(embed=embed, view=GiveawayView())
        data = {"guild_id": interaction.guild.id, "channel_id": channel.id, "message_id": message.id, "prize": premio, "winners": ganhadores, "end_at": end_at, "entrants": [], "ended": False}
        await set_data(self.redis, key(interaction.guild.id, f"giveaway:{message.id}"), data)
        await self.redis.sadd(key(interaction.guild.id, "giveaways:active"), str(message.id))
        await self.redis.sadd("scpro:feature:active-guilds", str(interaction.guild.id))

    @tasks.loop(seconds=30)
    async def giveaway_worker(self):
        try:
            raw_guilds = await self.redis.smembers("scpro:feature:active-guilds")
            for raw_gid in raw_guilds:
                gid = int(raw_gid.decode() if isinstance(raw_gid, bytes) else raw_gid)
                active = await self.redis.smembers(key(gid, "giveaways:active"))
                for raw_mid in active:
                    mid = int(raw_mid.decode() if isinstance(raw_mid, bytes) else raw_mid)
                    data_key = key(gid, f"giveaway:{mid}")
                    data = await get_data(self.redis, data_key, None)
                    if not data or data.get("ended") or int(data.get("end_at", 0)) > int(time.time()):
                        continue
                    guild = self.bot.get_guild(gid)
                    channel = guild.get_channel(int(data["channel_id"])) if guild else None
                    message = None
                    if isinstance(channel, discord.TextChannel):
                        try:
                            message = await channel.fetch_message(mid)
                        except discord.HTTPException:
                            pass
                    entrants = list(dict.fromkeys(data.get("entrants", [])))
                    winners = random.sample(entrants, min(int(data.get("winners", 1)), len(entrants))) if entrants else []
                    if isinstance(channel, discord.TextChannel) and message:
                        embed = message.embeds[0] if message.embeds else discord.Embed(title="Sorteio encerrado")
                        embed.color = discord.Color.dark_grey()
                        embed.add_field(name="Resultado", value=", ".join(f"<@{uid}>" for uid in winners) if winners else "Ninguém participou.", inline=False)
                        try:
                            await message.edit(embed=embed, view=None)
                            if winners:
                                await channel.send(f"Parabéns {', '.join(f'<@{uid}>' for uid in winners)}! Você ganhou {discord.utils.escape_mentions(data['prize'])}.")
                        except discord.HTTPException:
                            log.exception("Falha ao encerrar sorteio")
                    data["ended"] = True
                    data["winners_selected"] = winners
                    await set_data(self.redis, data_key, data)
                    await self.redis.srem(key(gid, "giveaways:active"), str(mid))
        except Exception:
            log.exception("Erro no processador de sorteios")

    @giveaway_worker.before_loop
    async def before_giveaway_worker(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self.redis.sadd("scpro:feature:active-guilds", str(guild.id))
        log.info("Sistemas extras carregados em %s servidores.", len(self.bot.guilds))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        settings = await self.bot.get_settings(member.guild.id)
        role_id = settings.get("autorole_id")
        role = member.guild.get_role(int(role_id)) if role_id else None
        if role and member.guild.me and role < member.guild.me.top_role:
            try:
                await member.add_roles(role, reason="Cargo automático Studio Criativo Pro")
            except discord.HTTPException:
                log.exception("Falha ao atribuir cargo automático")
        raid_key = key(member.guild.id, "raid-joins:20s")
        joins = await self.redis.incr(raid_key)
        if int(joins) == 1:
            await self.redis.expire(raid_key, 20)
        if int(joins) >= 8 and int(joins) in (8, 12, 16, 20):
            await feature_log(self.bot, member.guild, f"ALERTA ANTI-RAID: {joins} entradas em 20 segundos. Verifique o servidor.")
        await feature_log(self.bot, member.guild, f"Novo membro: {member.mention}.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        gid, uid = message.guild.id, message.author.id
        if len(message.content.strip()) >= 3:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            mission_key = key(gid, f"mission-count:{day}:{uid}")
            count = await self.redis.incr(mission_key)
            if int(count) == 1:
                await self.redis.expire(mission_key, 172800)
        if not isinstance(message.author, discord.Member) or message.author.guild_permissions.manage_messages:
            return
        spam_key = key(gid, f"spam:{uid}:8s")
        count = await self.redis.incr(spam_key)
        if int(count) == 1:
            await self.redis.expire(spam_key, 8)
        if int(count) >= 6:
            first = await self.redis.set(key(gid, f"spam-muted:{uid}"), "1", nx=True, ex=300)
            if first:
                try:
                    await message.author.timeout(datetime.now(timezone.utc) + timedelta(minutes=5), reason="Anti-spam automático")
                except discord.HTTPException:
                    log.exception("Anti-spam detectou flood, mas não conseguiu aplicar timeout")
                try:
                    await message.channel.send(f"{message.author.mention}, reduza a velocidade das mensagens.", delete_after=8)
                except discord.HTTPException:
                    pass
                await feature_log(self.bot, message.guild, f"Anti-spam acionado para {message.author.mention} em {message.channel.mention}.")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await feature_log(self.bot, member.guild, f"Membro saiu: {discord.utils.escape_markdown(member.display_name)}.")


async def setup(bot):
    await bot.add_cog(StudioFeatures(bot))
    bot.add_view(TicketView())
    bot.add_view(GiveawayView())
