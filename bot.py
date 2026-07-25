import discord
from discord.ext import commands
from discord.ui import View, Button
import os
import threading
import asyncio
import json
import random
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from config import *

# ── Firebase ────────────────────────────────
import firebase_admin
from firebase_admin import credentials, firestore

firebase_creds = os.environ.get("FIREBASE_CREDENTIALS")
if firebase_creds:
    cred_dict = json.loads(firebase_creds)
    cred = credentials.Certificate(cred_dict)
else:
    cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred)
db_admin = firestore.client()

# ── Web server ──────────────────────────────
class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"RFE Valorant Bot is running!")

    def do_POST(self):
        if self.path == "/assign-role":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            discord_id = body.get("discord_id")
            team_nom   = body.get("team_nom")
            zone       = body.get("zone", "OPEN")
            attente    = body.get("attente", False)
            asyncio.run_coroutine_threadsafe(
                assign_role_inscription(discord_id, team_nom, zone, attente),
                bot.loop
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), WebHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ── Bot ─────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ============================================
# ATTRIBUTION RÔLES
# ============================================
async def assign_role_inscription(discord_id: str, team_nom: str, zone: str, attente: bool = False):
    guild = bot.get_guild(GUILD_ID)
    if not guild or not discord_id:
        return
    try:
        member = guild.get_member(int(discord_id))
        if not member:
            return

        if attente:
            role_attente = guild.get_role(ROLE_ATTENTE_OPEN_ID)
            if role_attente:
                await member.add_roles(role_attente)
        else:
            role_zone_id = ROLE_RFE_OPEN_ID if zone == "OPEN" else ROLE_RFE_ASCENDANT_ID
            role_part_id = ROLE_PARTICIPANT_OPEN_ID if zone == "OPEN" else ROLE_PARTICIPANT_ASCENDANT_ID
            role_zone = guild.get_role(role_zone_id)
            role_part = guild.get_role(role_part_id)
            role_captain = guild.get_role(ROLE_CAPTAIN_ID)
            roles_to_add = [r for r in [role_zone, role_part, role_captain] if r]
            if roles_to_add:
                await member.add_roles(*roles_to_add)
            try:
                await member.edit(nick=f"[VAL] {team_nom}")
            except discord.Forbidden:
                pass
        print(f"✅ Rôles Valorant attribués : {team_nom} ({discord_id}) — {zone}")
    except Exception as e:
        print(f"❌ assign_role error: {e}")


# ============================================
# CRÉATION SALONS TOURNOI VALORANT
# ============================================
async def creer_salons_tournoi(tournoi_id: str, tournoi: dict):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    nom    = tournoi.get("nom", "Tournoi RFE Valorant")
    zone   = tournoi.get("zone", "OPEN")
    equipes = int(tournoi.get("equipes", 16))
    format_ = tournoi.get("format", "poules")

    emoji = "🎯"
    role_part_id = ROLE_PARTICIPANT_OPEN_ID if zone == "OPEN" else ROLE_PARTICIPANT_ASCENDANT_ID
    role_participant = guild.get_role(role_part_id)

    # Permissions base
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    if role_participant:
        overwrites[role_participant] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    for staff_id in ROLES_STAFF_IDS:
        role_staff = guild.get_role(staff_id)
        if role_staff:
            overwrites[role_staff] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    # Lecture seule
    overwrites_ro = dict(overwrites)
    if role_participant:
        overwrites_ro[role_participant] = discord.PermissionOverwrite(view_channel=True, send_messages=False)

    # Staff only
    overwrites_staff = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    for staff_id in ROLES_STAFF_IDS:
        role_staff = guild.get_role(staff_id)
        if role_staff:
            overwrites_staff[role_staff] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    # Crée la catégorie
    categorie = await guild.create_category(f"{emoji} {nom}", overwrites=overwrites)

    # Salons texte
    annonces = await guild.create_text_channel("📋・annonces-tournoi", category=categorie, overwrites=overwrites_ro)
    await guild.create_text_channel("📊・matchs-general", category=categorie, overwrites=overwrites_ro)
    await guild.create_text_channel("📝・rentrer-son-score", category=categorie, overwrites=overwrites_ro)
    await guild.create_text_channel("📺・liens-stream", category=categorie, overwrites=overwrites)
    await guild.create_text_channel("🛡️・tournoi-log", category=categorie, overwrites=overwrites_staff)

    # Salons poules
    if format_ in ["poules", "poules_only"]:
        nb_poules = max(2, equipes // 5)  # 5 équipes par poule en Valorant
        lettres = "ABCDEFGH"
        for i in range(min(nb_poules, 8)):
            await guild.create_text_channel(f"🎯・poule-{lettres[i].lower()}", category=categorie, overwrites=overwrites)
            await guild.create_text_channel(f"📅・matchs-poule-{lettres[i].lower()}", category=categorie, overwrites=overwrites_ro)

    # Phases finales
    await guild.create_text_channel("🏆・phases-finales", category=categorie, overwrites=overwrites)
    await guild.create_text_channel("📅・matchs-phases-finales", category=categorie, overwrites=overwrites_ro)
    await guild.create_text_channel("📊・resultats", category=categorie, overwrites=overwrites_ro)

    # Vocaux
    overwrites_voc = dict(overwrites)
    await guild.create_voice_channel("📺 Diffusion Match 1", category=categorie, overwrites=overwrites_voc)
    await guild.create_voice_channel("📺 Diffusion Match 2", category=categorie, overwrites=overwrites_voc)
    await guild.create_voice_channel("🎙️ Loge Casteur", category=categorie, overwrites=overwrites_staff)

    # Message annonce
    embed = discord.Embed(
        title=f"🎯 {nom}",
        description="Nouveau tournoi Valorant RFE !",
        color=0xFF4655  # Rouge Valorant
    )
    embed.add_field(name="📅 Date", value=tournoi.get("date", "À confirmer"), inline=True)
    embed.add_field(name="👥 Équipes", value=f"{equipes} max", inline=True)
    embed.add_field(name="🎮 Format", value=format_.replace("_", " ").title(), inline=True)
    if tournoi.get("cashprize"):
        embed.add_field(name="💰 Cashprize", value=tournoi.get("cashprize"), inline=False)
    embed.set_footer(text="RFE | Valorant — Powered by RFE Bot")
    await annonces.send(embed=embed)

    # Sauvegarde
    db_admin.collection("val_tournois").document(tournoi_id).update({
        "discord_categorie_id": str(categorie.id),
        "statut": "actif"
    })
    print(f"✅ Salons Valorant créés : {nom}")


# ============================================
# SUPPRESSION SALONS
# ============================================
async def supprimer_salons_tournoi(tournoi_id: str):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    doc = db_admin.collection("val_tournois").document(tournoi_id).get()
    if not doc.exists:
        return

    data = doc.to_dict()
    zone = data.get("zone", "OPEN")
    cat_id = data.get("discord_categorie_id")

    # Retire les rôles
    role_part_id = ROLE_PARTICIPANT_OPEN_ID if zone == "OPEN" else ROLE_PARTICIPANT_ASCENDANT_ID
    role_part = guild.get_role(role_part_id)
    roles_a_retirer = [r for r in [role_part] if r]

    for member in guild.members:
        roles_retirer = [r for r in roles_a_retirer if r.id in [mr.id for mr in member.roles]]
        if roles_retirer:
            try:
                await member.remove_roles(*roles_retirer)
            except discord.Forbidden:
                pass

    # Supprime les salons
    if cat_id:
        categorie = guild.get_channel(int(cat_id))
        if categorie and isinstance(categorie, discord.CategoryChannel):
            for salon in categorie.channels:
                await salon.delete()
            await categorie.delete()
    print(f"✅ Salons Valorant supprimés : {tournoi_id}")


# ============================================
# ONBOARDING — Bouton S'inscrire
# ============================================
class InscriptionView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎯 Rejoindre RFE Valorant", style=discord.ButtonStyle.danger, custom_id="btn_valo_open")
    async def rejoindre(self, interaction: discord.Interaction, button: Button):
        role_open = interaction.guild.get_role(ROLE_RFE_OPEN_ID)
        await interaction.response.send_message(
            "🎯 **RFE Valorant OPEN**\n"
            "Tournois ouverts à tous les niveaux.\n\n"
            "Inscris ton équipe sur **rfe-esport.fr/valorant** pour participer aux tournois !",
            ephemeral=True
        )
        if role_open:
            try:
                await interaction.user.add_roles(role_open)
            except discord.Forbidden:
                pass


# ============================================
# FIRESTORE LISTENER
# ============================================
def start_firestore_listener():
    processed = set()
    first_run = [True]

    def on_snapshot(col_snapshot, changes, read_time):
        if first_run[0]:
            for change in changes:
                processed.add(change.document.id)
            first_run[0] = False
            print(f"📋 {len(processed)} tournois Valorant existants chargés")
            return

        for change in changes:
            if change.type.name == "ADDED":
                tournoi_id = change.document.id
                tournoi    = change.document.to_dict()
                if tournoi_id in processed:
                    continue
                processed.add(tournoi_id)
                if tournoi.get("discord_categorie_id") or tournoi.get("creation_en_cours"):
                    continue
                if tournoi.get("statut") == "termine":
                    continue
                print(f"🆕 Nouveau tournoi Valorant : {tournoi.get('nom')}")
                db_admin.collection("val_tournois").document(tournoi_id).update({"creation_en_cours": True})
                asyncio.run_coroutine_threadsafe(
                    creer_salons_tournoi(tournoi_id, tournoi),
                    bot.loop
                )

            elif change.type.name == "MODIFIED":
                tournoi_id = change.document.id
                tournoi    = change.document.to_dict()
                if tournoi.get("statut") == "termine" and tournoi.get("discord_categorie_id"):
                    asyncio.run_coroutine_threadsafe(
                        supprimer_salons_tournoi(tournoi_id),
                        bot.loop
                    )

    db_admin.collection("val_tournois").on_snapshot(on_snapshot)


# ============================================
# COMMANDES
# ============================================
@bot.command(name="setup_inscription")
@commands.has_permissions(administrator=True)
async def setup_inscription(ctx):
    channel = bot.get_channel(CHANNEL_S_INSCRIRE_ID)
    if not channel:
        await ctx.send("❌ Salon #s-inscrire introuvable")
        return

    embed = discord.Embed(
        title="🎯 RFE | Valorant",
        description=(
            "Bienvenue dans la section Valorant de RFE !\n\n"
            "🎯 **RFE OPEN** — Ouvert à tous les niveaux\n"
            "🔒 **RFE ASCENDANT** — Bientôt disponible\n\n"
            "Clique ci-dessous pour rejoindre la communauté !"
        ),
        color=0xFF4655
    )
    embed.set_footer(text="RFE | Valorant — Powered by RFE Bot")
    await channel.send(embed=embed, view=InscriptionView())
    await ctx.send(f"✅ Message envoyé dans {channel.mention}", delete_after=5)


# ============================================
# DÉMARRAGE
# ============================================
@bot.event
async def on_ready():
    bot.add_view(InscriptionView())
    print(f"✅ RFE Valorant Bot connecté : {bot.user}")
    threading.Thread(target=start_firestore_listener, daemon=True).start()
    print("🔥 Listener Firestore Valorant actif")

bot.run(os.environ["DISCORD_TOKEN"])
