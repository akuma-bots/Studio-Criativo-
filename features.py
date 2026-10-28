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

# Instalação dedicada ao STUDIO DRACONIC: canais e Pix fixos.
STUDIO_CHANNELS = {
    "welcome_channel": 1557571732624113745,
    "functioning_channel": 1557572016188432474,
    "terms_channel": 1557572074338390077,
    "booster_channel": 1557572137781567578,
    "store_catalog_channel": 1557572244383989760,
    "products_channel": 1557573029238800488,
    "purchase_log_channel": 1565016859131121725,
    "ticket_channel": 1557630942854512650,
    "ticket_feedback_channel": 1557568292644716636,
    "ticket_log_channel": 1557630941596356671,
    "moderation_log_channel": 1565018251124146369,
    "log_channel": 1565018509694738502,
}
PIX_KEY_KAUA = "14499092990"
PIX_KEY_KAUAN = "173.867.024.42"  # Mantida exatamente como fornecida.
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


def panel_topic(title):
    """Identificador semântico estável para reconhecer painéis antigos com títulos diferentes."""
    normalized = (title or "").casefold()
    if any(word in normalized for word in ("central de atendimento", "painel de tickets", "atendimento oficial")):
        return "ticket-panel"
    if any(word in normalized for word in ("catálogo", "catalogo", "loja gta rp")):
        return "catalog"
    if any(word in normalized for word in ("tabela", "preços", "precos")):
        return "prices"
    # Títulos antigos e atuais devem convergir para o mesmo painel tutorial.
    if any(word in normalized for word in ("guia da loja", "tutorial", "como funciona", "funcionamento")):
        return "tutorial"
    return None


async def upsert_bot_message(channel, embed, view=None, limit=200):
    """Mantém uma única mensagem fixa por assunto: atualiza a existente e remove duplicatas antigas."""
    if not isinstance(channel, discord.TextChannel) or channel.guild.me is None:
        return None
    title = embed.title
    topic = panel_topic(title)
    # Identidade visual consistente, usando o ícone do servidor quando disponível.
    if channel.guild.icon:
        embed.set_author(name="STUDIO DRACONIC  •  CENTRAL OFICIAL", icon_url=channel.guild.icon.url)
    else:
        embed.set_author(name="STUDIO DRACONIC  •  CENTRAL OFICIAL")
    embed.timestamp = datetime.now(timezone.utc)
    try:
        matches = []
        async for message in channel.history(limit=limit, oldest_first=False):
            if message.author.id != channel.guild.me.id or not message.embeds:
                continue
            old_title = message.embeds[0].title
            if old_title == title or (topic and panel_topic(old_title) == topic):
                matches.append(message)
        if matches:
            # Mantém a mensagem mais recente e limpa duplicatas criadas por versões antigas.
            keeper = matches[0]
            await keeper.edit(embed=embed, view=view)
            for duplicate in matches[1:]:
                try:
                    await duplicate.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    log.warning("Não consegui remover painel duplicado %s no canal %s", duplicate.id, channel.id)
            return keeper
        return await channel.send(embed=embed, view=view)
    except discord.HTTPException:
        log.exception("Falha ao atualizar painel %r no canal %s", title, channel.id)
        return None


STORE_CATEGORIES = {
    "roupas": ("👕 Roupas & Uniformes", "Uniformes de facção, conjuntos casuais, roupas sociais e peças personalizadas para seu personagem."),
    "acessorios": ("🧢 Acessórios", "Bonés, correntes, máscaras, óculos e outros acessórios compatíveis com o estilo do seu personagem."),
    "pacotes": ("📦 Pacotes RP", "Combos de roupas, conjuntos exclusivos e pacotes personalizados para grupos e facções."),
    "personalizados": ("🎨 Personalizados", "Desenvolvimento de peças sob medida, identidade visual de facção e uniformes exclusivos."),
}


class StoreTutorialView(discord.ui.View):
    PAGES = [
        ("👋 Bem-vindo à loja", "O **Studio Draconic** cria roupas, uniformes e peças personalizadas para servidores de **GTA RP**. Leia as etapas antes de iniciar seu pedido.\n\n🔒 Nunca envie senhas, códigos de autenticação ou acesso à sua conta."),
        ("1️⃣ Escolha o produto", "Confira o catálogo e escolha a categoria: **roupas e uniformes**, **acessórios**, **pacotes RP** ou **personalizados**.\n\nSe for uma encomenda personalizada, prepare referências, nome da cidade/servidor, modelo desejado e quantidade."),
        ("2️⃣ Abra seu ticket", "Clique em **Fazer pedido** no catálogo. O bot abrirá um canal privado para você e a equipe.\n\nEnvie os detalhes do pedido e, se necessário, imagens de referência. Não abra vários tickets para a mesma compra."),
        ("3️⃣ Confirme o orçamento", "A equipe confirmará o produto, o preço final, o prazo e os detalhes da entrega. **Não pague antes de confirmar o valor no ticket oficial.**"),
        ("4️⃣ Pague via Pix", "Use somente os dados de Pix informados pela equipe no ticket oficial. Confira o destinatário e o valor antes de pagar.\n\nA confirmação do pagamento é feita pela equipe; nunca envie senha bancária ou código de autenticação."),
        ("5️⃣ Receba seu produto", "Após a equipe confirmar o pagamento e finalizar o pedido, o produto ou as instruções serão enviados conforme combinado. Confira suas mensagens privadas no Discord e permita DMs do servidor."),
        ("🆘 Precisa de ajuda?", "Se tiver dúvidas, responda no próprio ticket para manter o histórico do pedido. Não confie em pessoas que chamem no privado pedindo pagamento fora do procedimento oficial.")
    ]

    def __init__(self):
        super().__init__(timeout=900)
        self.page = 0
        self._refresh_buttons()

    def _refresh_buttons(self):
        self.clear_items()
        prev_button = discord.ui.Button(label="Anterior", emoji="⬅️", style=discord.ButtonStyle.secondary, disabled=self.page == 0)
        next_button = discord.ui.Button(label="Próxima etapa", emoji="➡️", style=discord.ButtonStyle.primary, disabled=self.page == len(self.PAGES) - 1)
        prev_button.callback = self.previous_page
        next_button.callback = self.next_page
        self.add_item(prev_button)
        self.add_item(next_button)
        start_button = discord.ui.Button(label="Iniciar pedido", emoji="♛", style=discord.ButtonStyle.primary)
        start_button.callback = self.start_order
        self.add_item(start_button)

    def _embed(self):
        title, body = self.PAGES[self.page]
        embed = discord.Embed(title=f"🐉 STUDIO DRACONIC • GUIA DA LOJA • {title}", description=body, color=discord.Color.from_rgb(125, 75, 220))
        embed.set_footer(text=f"Studio Draconic • Etapa {self.page + 1} de {len(self.PAGES)}")
        return embed

    async def previous_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.page = min(len(self.PAGES) - 1, self.page + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def start_order(self, interaction: discord.Interaction):
        await StoreOrderView().children[0].callback(interaction)


class ProductSelect(discord.ui.Select):
    def __init__(self, guild_id, products):
        self.guild_id = guild_id
        options = [discord.SelectOption(label=str(item["name"])[:100], value=str(pid), description=f'{item.get("price", "Consultar")} • {item.get("description", "Produto da loja")}'[:100], emoji="👕") for pid, item in list(products.items())[:25]]
        super().__init__(placeholder="Escolha o produto que deseja comprar...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await ephemeral(interaction, "Use esta opção dentro do servidor.")
        settings = await interaction.client.get_settings(guild.id)
        product_id = self.values[0]
        products = await get_data(interaction.client.redis, key(guild.id, "store:products"), {})
        product = products.get(product_id)
        if not product or not product.get("active", True):
            return await ephemeral(interaction, "Este produto não está mais disponível. Atualize o catálogo.")
        existing = discord.utils.find(lambda ch: isinstance(ch, discord.TextChannel) and ch.topic and f"scpro-ticket-owner:{interaction.user.id}" in ch.topic and "scpro-order:true" in ch.topic, guild.text_channels)
        if existing:
            return await ephemeral(interaction, f"Você já tem um pedido em andamento: {existing.mention}")
        category = guild.get_channel(int(settings["ticket_category"])) if settings.get("ticket_category") else None
        if not isinstance(category, discord.CategoryChannel):
            category = None
        me = guild.me
        if me is None:
            return await ephemeral(interaction, "Não consegui verificar as permissões do bot.")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        }
        staff_role = guild.get_role(int(settings["ticket_staff_role"])) if settings.get("ticket_staff_role") else None
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        order_id = f"SC-{int(time.time())}-{interaction.user.id % 10000:04d}"
        channel = await guild.create_text_channel(name=f"pedido-{interaction.user.name[:12]}".lower().replace(" ", "-"), category=category, overwrites=overwrites, topic=f"Pedido {order_id} | scpro-ticket-owner:{interaction.user.id} scpro-order:true", reason=f"Pedido da loja {order_id} aberto por {interaction.user}")
        pix_key_kaua = PIX_KEY_KAUA
        pix_key_kauan = PIX_KEY_KAUAN
        embed = discord.Embed(title=f"🐉 STUDIO DRACONIC • PEDIDO {order_id}", description=f"Olá {interaction.user.mention}! Seu pedido foi criado. A equipe vai confirmar os detalhes antes do pagamento.", color=discord.Color.from_rgb(155, 35, 255))
        embed.add_field(name="Produto", value=discord.utils.escape_mentions(product["name"]), inline=True)
        embed.add_field(name="Valor", value=discord.utils.escape_mentions(str(product.get("price", "A confirmar"))), inline=True)
        embed.add_field(name="Tipo", value="Por encomenda" if product.get("made_to_order") else "Produto pronto", inline=True)
        embed.add_field(name="Descrição", value=discord.utils.escape_mentions(product.get("description", "Sem descrição"))[:1024], inline=False)
        pix_lines = []
        if pix_key_kaua:
            pix_lines.append("**Kauã Gonsalves** — " + discord.utils.escape_markdown(str(pix_key_kaua)))
        if pix_key_kauan:
            pix_lines.append("**Kauan Salles** — " + discord.utils.escape_markdown(str(pix_key_kauan)))
        pix_lines.append("Pague somente após a equipe confirmar o valor. Confira o nome do destinatário antes de concluir.")
        embed.add_field(name="Pagamento via Pix", value="\n".join(pix_lines)[:1024], inline=False)
        embed.add_field(name="✦ ETAPAS DO PEDIDO", value="`01`  Confirme modelo, quantidade e prazo com a equipe.\n`02`  Aguarde a confirmação do valor final.\n`03`  Confira o titular e pague via Pix.\n`04`  A equipe confirma o recebimento manualmente.\n`05`  A entrega é liberada após a validação.", inline=False)
        await channel.send(content=interaction.user.mention, embed=embed, view=TicketView())
        order = {"order_id": order_id, "guild_id": guild.id, "channel_id": channel.id, "user_id": interaction.user.id, "product_id": product_id, "product_name": product["name"], "price": product.get("price", "A confirmar"), "status": "aguardando_pagamento", "created_at": int(time.time())}
        await set_data(interaction.client.redis, key(guild.id, f"store:order:{order_id}"), order)
        log_channel_id = settings.get("purchase_log_channel")
        log_channel = guild.get_channel(int(log_channel_id)) if log_channel_id else None
        if isinstance(log_channel, discord.TextChannel):
            log_embed = discord.Embed(title=f"🐉 NOVO PEDIDO • {order_id}", color=discord.Color.orange())
            log_embed.add_field(name="Cliente", value=f"{interaction.user.mention} (ID: {interaction.user.id})", inline=False)
            log_embed.add_field(name="Produto", value=discord.utils.escape_mentions(product["name"]), inline=True)
            log_embed.add_field(name="Valor", value=discord.utils.escape_mentions(str(product.get("price", "A confirmar"))), inline=True)
            log_embed.add_field(name="Status", value="Aguardando pagamento", inline=True)
            log_embed.add_field(name="Ticket", value=channel.mention, inline=False)
            await log_channel.send(embed=log_embed)
        await ephemeral(interaction, f"Pedido {order_id} criado! Continue no ticket: {channel.mention}")
        try:
            await interaction.client.log_event(guild, f"🛍️ Pedido {order_id} aberto por {interaction.user.mention}: {channel.mention}")
        except Exception:
            log.exception("Falha ao registrar pedido")


class ProductSelectView(discord.ui.View):
    def __init__(self, guild_id, products):
        super().__init__(timeout=180)
        self.add_item(ProductSelect(guild_id, products))


class StoreOrderView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Escolher pacote", style=discord.ButtonStyle.primary, emoji="🐉", custom_id="scpro:store:order")
    async def order(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este painel dentro do servidor.")
        products = await get_data(interaction.client.redis, key(interaction.guild.id, "store:products"), {})
        products = {pid: item for pid, item in products.items() if item.get("active", True)}
        if not products:
            return await ephemeral(interaction, "A loja ainda não tem produtos cadastrados. A equipe deve usar /produto_cadastrar.")
        await interaction.response.send_message("Selecione o produto para iniciar sua compra por ticket:", view=ProductSelectView(interaction.guild.id, products), ephemeral=True)
        return
        guild = interaction.guild
        existing = discord.utils.find(lambda ch: isinstance(ch, discord.TextChannel) and ch.topic and f"scpro-ticket-owner:{interaction.user.id}" in ch.topic and "scpro-order:true" in ch.topic, guild.text_channels)
        if existing:
            return await ephemeral(interaction, f"Você já tem um pedido em andamento: {existing.mention}")
        category = guild.get_channel(int(settings["ticket_category"])) if settings.get("ticket_category") else None
        if not isinstance(category, discord.CategoryChannel):
            category = None
        me = guild.me
        if me is None:
            return await ephemeral(interaction, "Não consegui verificar as permissões do bot.")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        }
        staff_role = guild.get_role(int(settings["ticket_staff_role"])) if settings.get("ticket_staff_role") else None
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await guild.create_text_channel(
            name=f"pedido-{interaction.user.name[:12]}".lower().replace(" ", "-"),
            category=category, overwrites=overwrites,
            topic=f"Pedido de roupa GTA RP | scpro-ticket-owner:{interaction.user.id} scpro-order:true",
            reason=f"Pedido da loja aberto por {interaction.user}"
        )
        embed = discord.Embed(
            title="🛍️ Novo pedido • Studio Draconic",
            description=(f"Olá {interaction.user.mention}! Conte à equipe o que você procura.\n\n"
                         "**Para agilizar, envie:**\n"
                         "• Produto ou categoria desejada\n• Servidor/cidade de GTA RP\n"
                         "• Quantidade e detalhes do modelo\n• Referências ou imagens, se tiver\n\n"
                         "A equipe confirma preço, prazo e forma de pagamento neste ticket. **Não envie senhas ou dados de acesso.**"),
            color=discord.Color.from_rgb(155, 35, 255)
        )
        await channel.send(content=interaction.user.mention, embed=embed, view=TicketView())
        await ephemeral(interaction, f"Seu atendimento foi aberto: {channel.mention}")
        try:
            await interaction.client.log_event(guild, f"🛍️ Pedido da loja aberto por {interaction.user.mention}: {channel.mention}")
        except Exception:
            log.exception("Falha ao registrar pedido")


    @discord.ui.button(label="Consultar preços", style=discord.ButtonStyle.secondary, emoji="💜", custom_id="scpro:store:prices")
    async def show_prices(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="♛ STUDIO DRACONIC • TABELA DE PREÇOS",
            description="**COMBOS PROMOCIONAIS**\n\n**01 peça** — R$ 25,00\n**02 peças** — R$ 40,00\n**03 peças** — R$ 65,00\n**04 peças** — R$ 85,00\n**06 peças** — R$ 120,00\n**07 peças** — R$ 150,00\n**08 peças** — R$ 170,00\n**09 peças** — R$ 180,00\n\nPara quantidades diferentes, consulte a equipe no ticket. O valor e o prazo serão confirmados antes do pagamento.",
            color=discord.Color.from_rgb(155, 35, 255),
        )
        embed.set_footer(text="🐉 STUDIO DRACONIC • Preto & Roxo Neon")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Escolha o assunto do atendimento",
        min_values=1,
        max_values=1,
        custom_id="scpro:ticket:reason",
        options=[
            discord.SelectOption(label="Comprar Algo", description="Comprar um item, pacote ou encomenda personalizada.", emoji="🛍️", value="compra"),
            discord.SelectOption(label="Dúvidas", description="Pergunte sobre preços, prazos ou como funciona.", emoji="💬", value="duvidas"),
            discord.SelectOption(label="Suporte Técnico", description="Problemas com pagamento, pedido ou entrega.", emoji="🛠️", value="suporte"),
            discord.SelectOption(label="Resgatar Sorteio", description="Solicite seu prêmio de um sorteio vencido.", emoji="🎁", value="sorteio"),
            discord.SelectOption(label="Parceria", description="Propostas de parceria e divulgação.", emoji="🤝", value="parceria"),
            discord.SelectOption(label="Seja Patrocinado", description="Apresente seu canal ou projeto para patrocínio.", emoji="🎥", value="patrocinio"),
        ],
    )
    async def choose_reason(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild = interaction.guild
        if guild is None:
            return await ephemeral(interaction, "Use este painel dentro do servidor.")
        reason_map = {
            "compra": ("🛍️ Comprar Algo", "Informe o produto ou pacote desejado, a quantidade e referências. A equipe confirmará preço e prazo antes do pagamento."),
            "duvidas": ("💬 Dúvidas", "Conte sua dúvida sobre preços, prazos, personalização ou funcionamento da loja."),
            "suporte": ("🛠️ Suporte Técnico", "Explique o problema com o pedido, pagamento ou entrega. Inclua o número do pedido, se tiver."),
            "sorteio": ("🎁 Resgatar Sorteio", "Informe o sorteio ganho e envie as informações públicas necessárias para confirmar o prêmio."),
            "parceria": ("🤝 Parceria", "Apresente seu servidor/projeto, público, proposta e forma de divulgação."),
            "patrocinio": ("🎥 Seja Patrocinado", "Compartilhe seu canal, plataforma, métricas públicas e a proposta de patrocínio."),
        }
        selected = select.values[0]
        title, guidance = reason_map.get(selected, ("💬 Atendimento", "Descreva como a equipe pode ajudar."))
        existing = discord.utils.find(
            lambda ch: isinstance(ch, discord.TextChannel) and ch.topic and f"scpro-ticket-owner:{interaction.user.id}" in ch.topic,
            guild.text_channels,
        )
        if existing:
            return await ephemeral(interaction, f"Você já tem um atendimento aberto: {existing.mention}")
        settings = await interaction.client.get_settings(guild.id)
        category = guild.get_channel(int(settings["ticket_category"])) if settings.get("ticket_category") else None
        if not isinstance(category, discord.CategoryChannel):
            category = None
        me = guild.me
        if me is None:
            return await ephemeral(interaction, "Não consegui verificar as permissões do bot.")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        }
        staff_role = guild.get_role(int(settings["ticket_staff_role"])) if settings.get("ticket_staff_role") else None
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
        slug = selected if selected in {"compra", "duvidas", "suporte", "sorteio", "parceria", "patrocinio"} else "atendimento"
        safe_name = "".join(ch for ch in interaction.user.name.lower() if ch.isalnum() or ch in "-_")[:18] or "cliente"
        channel = await guild.create_text_channel(
            name=f"{slug}-{safe_name}",
            category=category,
            overwrites=overwrites,
            topic=f"Atendimento: {title} | scpro-ticket-owner:{interaction.user.id}",
            reason=f"Ticket de {title} aberto por {interaction.user}",
        )
        embed = discord.Embed(
            title=f"🐉 STUDIO DRACONIC  •  {title.upper()}",
            description=f"Olá {interaction.user.mention}, seu atendimento privado está pronto.\n\n{guidance}\n\n**COMO FUNCIONA**\n**01** Envie os detalhes necessários.\n**02** Aguarde a equipe responder neste canal.\n**03** Confirme as condições antes de realizar pagamentos.\n\n🔒 Este canal é privado. Nunca compartilhe senhas, tokens ou códigos de autenticação.",
            color=discord.Color.from_rgb(155, 35, 255),
        )
        embed.add_field(name="✦ Atendimento organizado", value="Mantenha todas as informações deste assunto neste ticket para facilitar o acompanhamento.", inline=False)
        embed.set_footer(text="🐉 STUDIO DRACONIC  •  Preto & Roxo Neon")
        await channel.send(content=interaction.user.mention, embed=embed, view=TicketView())
        await ephemeral(interaction, f"Seu atendimento de {title} foi criado: {channel.mention}")
        await feature_log(interaction.client, guild, f"Ticket ({title}) aberto por {interaction.user.mention}: {channel.mention}")

class TicketFeedbackModal(discord.ui.Modal, title="Avalie seu atendimento"):
    rating = discord.ui.TextInput(label="Nota de 1 a 5", placeholder="5", min_length=1, max_length=1, required=True)
    comment = discord.ui.TextInput(label="Comentário (opcional)", placeholder="Conte como foi sua experiência...", style=discord.TextStyle.paragraph, max_length=800, required=False)

    def __init__(self, ticket_channel: discord.TextChannel, owner_id: int):
        super().__init__(timeout=300, custom_id="scpro:ticket:feedback")
        self.ticket_channel = ticket_channel
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction):
        channel = self.ticket_channel
        guild = channel.guild
        try:
            rating = int(str(self.rating.value).strip())
        except ValueError:
            return await ephemeral(interaction, "Informe uma nota entre 1 e 5.")
        if rating < 1 or rating > 5:
            return await ephemeral(interaction, "A nota deve ser de 1 a 5.")
        comment = discord.utils.escape_mentions(str(self.comment.value or "").strip()) or "Sem comentário."
        feedback_channel = guild.get_channel(STUDIO_CHANNELS["ticket_feedback_channel"])
        if isinstance(feedback_channel, discord.TextChannel):
            embed = discord.Embed(
                title="💜 NOVO FEEDBACK DE ATENDIMENTO",
                description=f"**Cliente:** <@{self.owner_id}>\n**Avaliação:** {'⭐' * rating} ({rating}/5)\n**Ticket:** #{channel.name}\n\n**Comentário**\n{comment}",
                color=discord.Color.from_rgb(155, 35, 255),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="🐉 STUDIO DRACONIC • Experiência do cliente")
            await feedback_channel.send(embed=embed)
        history_channel = guild.get_channel(STUDIO_CHANNELS["ticket_log_channel"])
        if isinstance(history_channel, discord.TextChannel):
            transcript_lines = []
            try:
                async for msg in channel.history(limit=60, oldest_first=True):
                    body = discord.utils.escape_mentions(msg.content or "")
                    if msg.embeds:
                        body += " [embed: " + (msg.embeds[0].title or "mensagem incorporada") + "]"
                    if msg.attachments:
                        body += " [anexos: " + ", ".join(a.filename for a in msg.attachments) + "]"
                    transcript_lines.append(f"{msg.created_at.strftime('%d/%m/%Y %H:%M')} | {msg.author}: {body}")
            except discord.HTTPException:
                transcript_lines.append("Não foi possível recuperar todo o histórico.")
            transcript = "\n".join(transcript_lines) or "Ticket sem mensagens."
            if len(transcript) > 3500:
                transcript = transcript[:3500] + "\n… histórico truncado."
            embed = discord.Embed(
                title="📁 ATENDIMENTO ENCERRADO • HISTÓRICO",
                description=f"**Canal:** #{channel.name}\n**Cliente:** <@{self.owner_id}>\n**Encerrado por:** {interaction.user.mention}\n**Feedback:** {'⭐' * rating} ({rating}/5)\n\n**Transcrição recente**\n{transcript}",
                color=discord.Color.from_rgb(155, 35, 255),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="🐉 STUDIO DRACONIC • Histórico de atendimento")
            await history_channel.send(embed=embed)
        await ephemeral(interaction, "Obrigado! Seu feedback foi registrado. Este ticket será encerrado.")
        await asyncio.sleep(2)
        try:
            await channel.delete(reason=f"Ticket encerrado com feedback por {interaction.user}")
        except discord.HTTPException:
            log.exception("Falha ao excluir ticket após feedback")


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fechar atendimento", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="scpro:ticket:close")
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
        await interaction.response.send_modal(TicketFeedbackModal(channel, owner_id))

class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Participar", style=discord.ButtonStyle.primary, emoji="💜", custom_id="scpro:giveaway:join")
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

    # configurar_sistemas removido: canais definidos automaticamente para esta instalação.

    # configurar_canais_studio removido: canais definidos automaticamente para esta instalação.

    @app_commands.command(name="automod_status", description="Mostra o estado básico da automoderação")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def automod_status(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando dentro do servidor.")
        settings = await self.bot.get_settings(interaction.guild.id)
        channel_id = settings.get("moderation_log_channel")
        channel = interaction.guild.get_channel(int(channel_id)) if channel_id else None
        destination = channel.mention if isinstance(channel, discord.TextChannel) else "não configurado"
        await ephemeral(interaction, "🛡️ **Automoderação básica ativa**\n• Anti-spam: 6 mensagens em 8 segundos, com timeout de 5 minutos.\n• Anti-raid: alerta para 8 ou mais entradas em 20 segundos.\n• Filtro de mensagens repetidas e menções excessivas: ativo após a atualização.\n• Canal de moderação: " + destination)

    @app_commands.command(name="produto_cadastrar", description="Cadastra produto pronto ou por encomenda na loja")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def produto_cadastrar(self, interaction: discord.Interaction, nome: str, preco: str, descricao: str, entrega: str, por_encomenda: bool = False):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando dentro de um servidor.")
        if len(preco) > 50 or len(descricao) > 900 or len(entrega) > 1500:
            return await ephemeral(interaction, "Um dos campos passou do limite. Reduza o tamanho e tente novamente.")
        if not (preco.strip().startswith("R$") or preco.strip().lower() in ("consultar", "a combinar")):
            return await ephemeral(interaction, "Informe o preço como R$ 25,00, Consultar ou A combinar.")
        if not (entrega.startswith("https://") or entrega.startswith("http://") or por_encomenda):
            return await ephemeral(interaction, "Para produto pronto, informe um link HTTPS de entrega. Para produto por encomenda, marque por_encomenda=True.")
        products = await get_data(self.redis, key(interaction.guild.id, "store:products"), {})
        product_id = str(time.time_ns())[-12:]
        products[product_id] = {"name": nome.strip(), "price": preco.strip(), "description": descricao.strip(), "delivery": "" if por_encomenda else entrega.strip(), "made_to_order": por_encomenda, "active": True}
        await set_data(self.redis, key(interaction.guild.id, "store:products"), products)
        await ephemeral(interaction, f"Produto {discord.utils.escape_mentions(nome)} cadastrado com ID {product_id}. Ele aparecerá no seletor de compras do catálogo.")

    @app_commands.command(name="pedido_pago", description="Registra a confirmação manual de um pagamento Pix")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pedido_pago(self, interaction: discord.Interaction, pedido_id: str):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando dentro de um servidor.")
        order_key = key(interaction.guild.id, f"store:order:{pedido_id}")
        order = await get_data(self.redis, order_key, None)
        if not order:
            return await ephemeral(interaction, "Não encontrei esse pedido.")
        if order.get("status") not in ("aguardando_pagamento", "aguardando_confirmacao"):
            return await ephemeral(interaction, f"O pedido está no status {order.get('status')} e não pode ser confirmado agora.")
        order["status"] = "pago"
        order["paid_at"] = int(time.time())
        order["paid_by"] = interaction.user.id
        await set_data(self.redis, order_key, order)
        channel = interaction.guild.get_channel(int(order["channel_id"]))
        if isinstance(channel, discord.TextChannel):
            await channel.send(f"✅ Pagamento Pix do pedido {pedido_id} confirmado manualmente por {interaction.user.mention}. A equipe pode concluir a entrega.")
        settings = await self.bot.get_settings(interaction.guild.id)
        log_channel = interaction.guild.get_channel(int(settings["purchase_log_channel"])) if settings.get("purchase_log_channel") else None
        if isinstance(log_channel, discord.TextChannel):
            await log_channel.send(f"✅ Pedido {pedido_id} marcado como pago por {interaction.user.mention}.")
        await ephemeral(interaction, f"Pedido {pedido_id} marcado como pago. Confirme o recebimento real antes de usar este comando.")

    @app_commands.command(name="pedido_entregar", description="Entrega um produto pronto ou registra encomenda concluída")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pedido_entregar(self, interaction: discord.Interaction, pedido_id: str, link_ou_instrucoes: str = None):
        if interaction.guild is None:
            return await ephemeral(interaction, "Use este comando dentro de um servidor.")
        order_key = key(interaction.guild.id, f"store:order:{pedido_id}")
        order = await get_data(self.redis, order_key, None)
        if not order:
            return await ephemeral(interaction, "Não encontrei esse pedido.")
        if order.get("status") != "pago":
            return await ephemeral(interaction, "A entrega só pode ser feita após o pedido ser marcado como pago.")
        products = await get_data(self.redis, key(interaction.guild.id, "store:products"), {})
        product = products.get(order.get("product_id"), {})
        delivery = (link_ou_instrucoes or product.get("delivery") or "").strip()
        if not delivery:
            return await ephemeral(interaction, "Informe o link ou as instruções de entrega.")
        user = interaction.guild.get_member(int(order["user_id"]))
        if user is None:
            try:
                user = await interaction.guild.fetch_member(int(order["user_id"]))
            except discord.HTTPException:
                user = None
        if user is None:
            return await ephemeral(interaction, "Não consegui encontrar o cliente no servidor. Verifique se ele ainda é membro.")
        try:
            await user.send(embed=discord.Embed(title=f"🐉 STUDIO DRACONIC • PEDIDO {pedido_id} ENTREGUE", description=f"Produto: {discord.utils.escape_mentions(order['product_name'])}\n\n{discord.utils.escape_mentions(delivery)}\n\nObrigado por comprar no Studio Draconic!", color=discord.Color.from_rgb(155, 35, 255)))
        except discord.Forbidden:
            return await ephemeral(interaction, "Não consegui enviar DM ao cliente. Peça para habilitar mensagens diretas e tente novamente.")
        order["status"] = "entregue"
        order["delivered_at"] = int(time.time())
        order["delivered_by"] = interaction.user.id
        await set_data(self.redis, order_key, order)
        ticket = interaction.guild.get_channel(int(order["channel_id"]))
        if isinstance(ticket, discord.TextChannel):
            await ticket.send(f"📦 Pedido {pedido_id} entregue por {interaction.user.mention}. O produto foi enviado por mensagem privada ao cliente.")
        settings = await self.bot.get_settings(interaction.guild.id)
        log_channel = interaction.guild.get_channel(int(settings["purchase_log_channel"])) if settings.get("purchase_log_channel") else None
        if isinstance(log_channel, discord.TextChannel):
            await log_channel.send(f"📦 Pedido {pedido_id} entregue a <@{order['user_id']}>. Responsável: {interaction.user.mention}.")
        await ephemeral(interaction, f"Entrega do pedido {pedido_id} concluída e enviada por DM.")

    @app_commands.command(name="loja", description="Mostra os itens disponíveis")
    async def loja(self, interaction):
        embed = discord.Embed(title="🐉 STUDIO DRACONIC • LOJA OFICIAL", color=discord.Color.from_rgb(155, 35, 255))
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
        embed = discord.Embed(title="🐉 STUDIO DRACONIC • ATENDIMENTO", description=f"Olá {interaction.user.mention}! A equipe atenderá você aqui.\nAssunto: {discord.utils.escape_markdown(assunto)}", color=discord.Color.blurple())
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
        embed = discord.Embed(title="🐉 STUDIO DRACONIC • NOVA SUGESTÃO", description=discord.utils.escape_mentions(texto), color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
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
        embed = discord.Embed(title="🐉 STUDIO DRACONIC • SORTEIO", description=f"Prêmio: {discord.utils.escape_mentions(premio)}\nGanhadores: {ganhadores}\nEncerra: <t:{end_at}:R>\n\nClique no botão para participar!", color=discord.Color.gold())
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
        # Descobre o servidor dono pelos canais fixos e aplica os IDs automaticamente.
        owner_guild_ids = set()
        for channel_id in STUDIO_CHANNELS.values():
            channel = self.bot.get_channel(channel_id)
            if channel and channel.guild:
                owner_guild_ids.add(channel.guild.id)
        if len(owner_guild_ids) > 1:
            log.error("Os canais fixos do Studio estão distribuídos por mais de um servidor; verifique os IDs.")
        for guild in self.bot.guilds:
            if owner_guild_ids and guild.id not in owner_guild_ids:
                continue
            await self.redis.sadd("scpro:feature:active-guilds", str(guild.id))
            settings = await self.bot.get_settings(guild.id)
            changed = False
            for name, channel_id in STUDIO_CHANNELS.items():
                channel = guild.get_channel(channel_id)
                if isinstance(channel, discord.TextChannel) and settings.get(name) != channel_id:
                    settings[name] = channel_id
                    changed = True
            if not settings.get("ticket_category"):
                category = discord.utils.get(guild.categories, name="🎫・Atendimentos")
                if category is None and guild.me and guild.me.guild_permissions.manage_channels:
                    try:
                        category = await guild.create_category("🎫・Atendimentos", reason="Configuração automática do Studio Draconic")
                    except discord.HTTPException:
                        log.exception("Não foi possível criar a categoria de atendimentos")
                if category:
                    settings["ticket_category"] = category.id
                    changed = True
            # Cadastro inicial automático dos combos anunciados na tabela oficial.
            products_key = key(guild.id, "store:products")
            products = await get_data(self.redis, products_key, {})
            price_bundles = [
                ("bundle-1", "1 peça • Avulsa", "R$ 25,00", "Uma peça avulsa; preço conforme tabela oficial."),
                ("bundle-2", "2 peças • Combo M+F", "R$ 40,00", "Combo de 2 peças M+F; preço promocional."),
                ("bundle-3", "3 peças • 1 combo + 1 avulsa", "R$ 65,00", "Três peças: um combo e uma avulsa."),
                ("bundle-4", "4 peças • 2 combos", "R$ 85,00", "Quatro peças: dois combos."),
                ("bundle-6", "6 peças • 3 combos", "R$ 120,00", "Seis peças: três combos."),
                ("bundle-7", "7 peças • 3 combos + 1 avulsa", "R$ 150,00", "Sete peças: três combos e uma avulsa."),
                ("bundle-8", "8 peças • 4 combos", "R$ 170,00", "Oito peças: quatro combos."),
                ("bundle-9", "9 peças • 4 combos + 1 avulsa", "R$ 180,00", "Nove peças: quatro combos e uma avulsa."),
            ]
            seeded = False
            for pid, name, price, description in price_bundles:
                if pid not in products:
                    products[pid] = {"name": name, "price": price, "description": description, "delivery": "", "made_to_order": True, "active": True}
                    seeded = True
            if seeded:
                await set_data(self.redis, products_key, products)
            if changed:
                await self.bot.put_json(self.bot.settings_key(guild.id), settings)
            ticket_panel_channel = guild.get_channel(STUDIO_CHANNELS["ticket_channel"])
            panel = discord.Embed(title="🐉 CENTRAL DE ATENDIMENTO", description="**SEU ESPAÇO OFICIAL DE SUPORTE**\n\nAbra um atendimento privado com a equipe do **Studio Draconic**. Escolha abaixo a opção que melhor descreve o que você precisa e acompanhe tudo em um único lugar.\n\n> Atendimento organizado, histórico preservado e privacidade por padrão.\n> **Segurança:** nunca envie senhas, códigos de autenticação ou dados bancários.", color=discord.Color.from_rgb(135, 65, 255))
            panel.add_field(name="🛍️ COMPRAS", value="Catálogo, combos e encomendas personalizadas.", inline=True)
            panel.add_field(name="💬 DÚVIDAS", value="Preços, prazos e orientações.", inline=True)
            panel.add_field(name="🛠️ PEDIDOS", value="Pagamentos, entregas e suporte técnico.", inline=True)
            panel.add_field(name="✦ OUTROS ASSUNTOS", value="Sorteios • Parcerias • Patrocínios\n**Selecione uma opção no menu abaixo para começar.**", inline=False)
            panel.set_footer(text="🐉 STUDIO DRACONIC • Preto & Roxo Neon")
            await upsert_bot_message(ticket_panel_channel, panel, TicketPanelView())

            catalog = guild.get_channel(STUDIO_CHANNELS["store_catalog_channel"])
            if isinstance(catalog, discord.TextChannel):
                embed = discord.Embed(title="🐉 CATÁLOGO EXCLUSIVO", description="**DESIGN QUE DESTACA A IDENTIDADE DO SEU RP**\n\nRoupas, uniformes e combos personalizados para projetos de **GTA RP**. Escolha um pacote no menu para iniciar um atendimento privado e alinhar referências, personalização e prazo.\n\n✦ **Processo claro**  •  ✦ **Atendimento dedicado**  •  ✦ **Entrega combinada**", color=discord.Color.from_rgb(135, 65, 255))
                embed.add_field(name="✦ PACOTES EM DESTAQUE", value="**01 peça**  ·  R$ 25,00\n**02 peças • Combo M+F**  ·  R$ 40,00\n**03 peças**  ·  R$ 65,00\n**04 peças**  ·  R$ 85,00\n**06 peças**  ·  R$ 120,00\n**07 peças**  ·  R$ 150,00\n**08 peças**  ·  R$ 170,00\n**09 peças**  ·  R$ 180,00", inline=False)
                embed.add_field(name="✦ COMO COMPRAR", value="**01** Escolha o pacote no menu.\n**02** Continue no ticket privado.\n**03** Confirme detalhes, valor final e prazo.\n**04** Pague apenas após a confirmação oficial da equipe.", inline=False)
                embed.set_footer(text="STUDIO DRACONIC • Qualidade, confiança e suporte")
                await upsert_bot_message(catalog, embed, StoreOrderView())

            products_channel = guild.get_channel(STUDIO_CHANNELS["products_channel"])
            price_embed = discord.Embed(title="✦ TABELA OFICIAL DE PREÇOS", description="**PACOTES E VALORES PROMOCIONAIS**\nConfira as opções disponíveis. A equipe confirma o escopo, o valor final e o prazo no ticket antes de qualquer pagamento.", color=discord.Color.from_rgb(135, 65, 255))
            price_embed.add_field(name="Quantidade", value="1 peça\n2 peças\n3 peças\n4 peças\n6 peças\n7 peças\n8 peças\n9 peças", inline=True)
            price_embed.add_field(name="Tipo de compra", value="Avulsa\nCombo M+F\n1 combo + 1 avulsa\n2 combos\n3 combos\n3 combos + 1 avulsa\n4 combos\n4 combos + 1 avulsa", inline=True)
            price_embed.add_field(name="Preço normal", value="R$ 25,00\nR$ 50,00\nR$ 75,00\nR$ 100,00\nR$ 150,00\nR$ 180,00\nR$ 200,00\nR$ 230,00", inline=True)
            price_embed.add_field(name="Total promocional", value="**R$ 25,00**\n**R$ 40,00**\n**R$ 65,00**\n**R$ 85,00**\n**R$ 120,00**\n**R$ 150,00**\n**R$ 170,00**\n**R$ 180,00**", inline=True)
            price_embed.set_footer(text="STUDIO DRACONIC  •  Valores em BRL  •  Confirmação oficial no ticket")
            await upsert_bot_message(products_channel, price_embed)

            target = guild.get_channel(STUDIO_CHANNELS["functioning_channel"])
            if isinstance(target, discord.TextChannel):
                tutorial = StoreTutorialView()
                await upsert_bot_message(target, tutorial._embed(), tutorial)
        log.info("Sistemas extras carregados; instalação dedicada inicializada.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        settings = await self.bot.get_settings(member.guild.id)
        role_id = settings.get("autorole_id")
        role = member.guild.get_role(int(role_id)) if role_id else None
        if role and member.guild.me and role < member.guild.me.top_role:
            try:
                await member.add_roles(role, reason="Cargo automático Studio Draconic Pro")
            except discord.HTTPException:
                log.exception("Falha ao atribuir cargo automático")
        raid_key = key(member.guild.id, "raid-joins:20s")
        joins = await self.redis.incr(raid_key)
        if int(joins) == 1:
            await self.redis.expire(raid_key, 20)
        if int(joins) >= 8 and int(joins) in (8, 12, 16, 20):
            await feature_log(self.bot, member.guild, f"ALERTA ANTI-RAID: {joins} entradas em 20 segundos. Verifique o servidor.")

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
        settings = await self.bot.get_settings(gid)
        moderation_channel_id = settings.get("moderation_log_channel")
        moderation_channel = message.guild.get_channel(int(moderation_channel_id)) if moderation_channel_id else None

        async def moderation_log(text):
            target = moderation_channel if isinstance(moderation_channel, discord.TextChannel) else None
            if target:
                try:
                    await target.send(text[:1900])
                except discord.HTTPException:
                    log.exception("Falha ao enviar log de automoderação")
            else:
                await feature_log(self.bot, message.guild, text)

        if len(message.mentions) >= 5 or len(message.role_mentions) >= 3:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            try:
                await message.author.timeout(datetime.now(timezone.utc) + timedelta(minutes=2), reason="Automod: excesso de menções")
            except discord.HTTPException:
                pass
            await moderation_log(f"🛡️ **Automod — menções excessivas**\nUsuário: {message.author.mention} (ID {uid})\nCanal: {message.channel.mention}\nAção: mensagem removida e timeout de 2 minutos.")
            return

        content = message.content.strip().casefold()
        if len(content) >= 5:
            repeat_key = key(gid, f"repeat:{uid}")
            previous = await self.redis.get(repeat_key)
            if isinstance(previous, bytes):
                previous = previous.decode("utf-8", errors="ignore")
            await self.redis.set(repeat_key, content[:500], ex=20)
            if previous == content[:500]:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                await moderation_log(f"🛡️ **Automod — mensagem repetida**\nUsuário: {message.author.mention} (ID {uid})\nCanal: {message.channel.mention}\nAção: mensagem repetida removida.")
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
                await moderation_log(f"🛡️ **Automod — anti-spam**\nUsuário: {message.author.mention} (ID {uid})\nCanal: {message.channel.mention}\nAção: timeout de 5 minutos por excesso de mensagens.")



async def setup(bot):
    await bot.add_cog(StudioFeatures(bot))
    bot.add_view(TicketView())
    bot.add_view(TicketPanelView())
    bot.add_view(GiveawayView())
    bot.add_view(StoreOrderView())
