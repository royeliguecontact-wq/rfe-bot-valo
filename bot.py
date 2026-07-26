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

import firebase_admin
from firebase_admin import credentials, firestore

firebase_creds = os.environ.get("FIREBASE_CREDENTIALS")
if firebase_creds:
    cred = credentials.Certificate(json.loads(firebase_creds))
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
            asyncio.run_coroutine_threadsafe(
                assign_role_inscription(body.get("discord_id"), body.get("team_nom"), body.get("zone", "OPEN"), body.get("attente", False)),
                bot.loop
            )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args): pass

threading.Thread(target=lambda: HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), WebHandler).serve_forever(), daemon=True).start()

# ── Bot ──────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

LETTRES_GROUPES = ["A", "B", "C", "D", "E", "F", "G", "H"]

# ── Attribution rôles ──────────────────────
async def assign_role_inscription(discord_id: str, team_nom: str, zone: str, attente: bool = False):
    guild = bot.get_guild(GUILD_ID)
    if not guild or not discord_id:
        return
    try:
        member = guild.get_member(int(discord_id))
        if not member:
            return
        if attente:
            role = guild.get_role(ROLE_ATTENTE_OPEN_ID)
            if role:
                await member.add_roles(role)
        else:
            role_zone = guild.get_role(ROLE_RFE_OPEN_ID if zone == "OPEN" else ROLE_RFE_ASCENDANT_ID)
            role_part = guild.get_role(ROLE_PARTICIPANT_OPEN_ID if zone == "OPEN" else ROLE_PARTICIPANT_ASCENDANT_ID)
            role_cap = guild.get_role(ROLE_CAPTAIN_ID)
            roles = [r for r in [role_zone, role_part, role_cap] if r]
            if roles:
                await member.add_roles(*roles)
            try:
                await member.edit(nick=f"[VAL] {team_nom}")
            except discord.Forbidden:
                pass
        print(f"✅ Rôles Valo attribués : {team_nom} ({discord_id})")
    except Exception as e:
        print(f"❌ assign_role error: {e}")

# ── Création salons tournoi ─────────────────
async def creer_salons_tournoi(tournoi_id: str, tournoi: dict):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    nom = tournoi.get("nom", "Tournoi RFE Valorant")
    zone = tournoi.get("zone", "OPEN")
    nb_equipes = int(tournoi.get("equipes", 16))

    role_part_id = ROLE_PARTICIPANT_OPEN_ID if zone == "OPEN" else ROLE_PARTICIPANT_ASCENDANT_ID
    role_participant = guild.get_role(role_part_id)

    # Permissions
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    if role_participant:
        overwrites[role_participant] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    for sid in ROLES_STAFF_IDS:
        r = guild.get_role(sid)
        if r:
            overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)

    overwrites_ro = dict(overwrites)
    if role_participant:
        overwrites_ro[role_participant] = discord.PermissionOverwrite(view_channel=True, send_messages=False)

    overwrites_staff = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    for sid in ROLES_STAFF_IDS:
        r = guild.get_role(sid)
        if r:
            overwrites_staff[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    # Catégorie
    categorie = await guild.create_category(f"🎯 {nom}", overwrites=overwrites)

    # Salons généraux
    annonces = await guild.create_text_channel("📋・annonces-tournoi", category=categorie, overwrites=overwrites_ro)
    await guild.create_text_channel("📊・matchs-general", category=categorie, overwrites=overwrites_ro)
    await guild.create_text_channel("🛡️・tournoi-log", category=categorie, overwrites=overwrites_staff)

    # Salons par groupe Swiss — visible uniquement par la poule concernée
    nb_groupes = max(2, nb_equipes // 4)
    for i in range(min(nb_groupes, 8)):
        lettre = LETTRES_GROUPES[i]
        role_poule = guild.get_role(ROLES_POULES_OPEN[i]) if i < len(ROLES_POULES_OPEN) else None

        # Permissions : staff + role poule uniquement
        ow_poule = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        for sid in ROLES_STAFF_IDS:
            r = guild.get_role(sid)
            if r:
                ow_poule[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        if role_poule:
            ow_poule[role_poule] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        # Matchs en lecture seule pour la poule
        ow_poule_ro = dict(ow_poule)
        if role_poule:
            ow_poule_ro[role_poule] = discord.PermissionOverwrite(view_channel=True, send_messages=False)

        await guild.create_text_channel(f"🎯・groupe-{lettre.lower()}", category=categorie, overwrites=ow_poule)
        await guild.create_text_channel(f"📅・matchs-groupe-{lettre.lower()}", category=categorie, overwrites=ow_poule_ro)

    # Phases finales — visible uniquement par @Phase Finale + staff
    role_pf = guild.get_role(ROLE_PHASE_FINALE_OPEN_ID)
    ow_pf = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    for sid in ROLES_STAFF_IDS:
        r = guild.get_role(sid)
        if r:
            ow_pf[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    if role_pf:
        ow_pf[role_pf] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    ow_pf_ro = dict(ow_pf)
    if role_pf:
        ow_pf_ro[role_pf] = discord.PermissionOverwrite(view_channel=True, send_messages=False)

    await guild.create_text_channel("🏆・phases-finales", category=categorie, overwrites=ow_pf)
    await guild.create_text_channel("📅・matchs-phases-finales", category=categorie, overwrites=ow_pf_ro)
    await guild.create_text_channel("📊・resultats", category=categorie, overwrites=overwrites_ro)

    # Loge Casteur uniquement (pas de vocaux diffusion, spectateur ingame)
    await guild.create_voice_channel("🎙️ Loge Casteur", category=categorie, overwrites=overwrites_staff)

    # Annonce
    embed = discord.Embed(
        title=f"🎯 {nom}",
        description="Nouveau tournoi Valorant RFE ! Les groupes Swiss seront annoncés prochainement.",
        color=0xFF4655
    )
    embed.add_field(name="📅 Format", value="Swiss BO1 → Playoffs BO3", inline=True)
    embed.add_field(name="👥 Équipes", value=f"{nb_equipes} max", inline=True)
    if tournoi.get("cashprize"):
        embed.add_field(name="💰 Cashprize", value=tournoi.get("cashprize"), inline=False)
    embed.set_footer(text="RFE | Valorant — Powered by RFE Bot")
    await annonces.send(embed=embed)

    db_admin.collection("val_tournois").document(tournoi_id).update({
        "discord_categorie_id": str(categorie.id),
        "statut": "actif"
    })
    print(f"✅ Salons Valorant créés : {nom}")

# ── Tirage Swiss ────────────────────────────
async def tirage_swiss(tournoi_id: str, ronde: int):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    doc = db_admin.collection("val_tournois").document(tournoi_id).get()
    if not doc.exists:
        return
    tournoi = doc.to_dict()
    cat_id = tournoi.get("discord_categorie_id")

    # Récupère les équipes avec leur bilan
    equipes_snap = db_admin.collection("val_tournois").document(tournoi_id).collection("equipes").get()
    equipes = [{"id": d.id, **d.data()} for d in equipes_snap]

    if not equipes:
        print(f"❌ Aucune équipe pour le tirage Swiss ronde {ronde}")
        return

    # Trie par victoires (desc) puis par différence de rounds
    equipes.sort(key=lambda e: (-e.get("victoires", 0), -e.get("diff_rounds", 0)))

    # Apparie les équipes — les mieux classées ensemble
    matchs = []
    utilisees = set()
    for i, eq in enumerate(equipes):
        if eq["id"] in utilisees:
            continue
        for j in range(i + 1, len(equipes)):
            adv = equipes[j]
            if adv["id"] in utilisees:
                continue
            # Vérifie qu'elles ne se sont pas déjà affrontées
            historique = eq.get("adversaires_passes", [])
            if adv["id"] not in historique:
                matchs.append((eq, adv))
                utilisees.add(eq["id"])
                utilisees.add(adv["id"])
                break

    # Sauvegarde les matchs Swiss dans Firestore
    for idx, (eq1, eq2) in enumerate(matchs):
        db_admin.collection("val_tournois").document(tournoi_id).collection("matchs_swiss").add({
            "ronde": ronde,
            "match_num": idx + 1,
            "equipe1": eq1["nom"],
            "equipe2": eq2["nom"],
            "equipe1_id": eq1["id"],
            "equipe2_id": eq2["id"],
            "statut": "en_attente",
            "createdAt": firestore.SERVER_TIMESTAMP
        })

    # Annonce dans le salon matchs-general
    if cat_id:
        categorie = guild.get_channel(int(cat_id))
        if categorie:
            matchs_ch = discord.utils.get(categorie.channels, name="matchs-general")
            if matchs_ch:
                embed = discord.Embed(
                    title=f"🎯 Ronde {ronde} — Appariements Swiss",
                    color=0xFF4655
                )
                for idx, (eq1, eq2) in enumerate(matchs):
                    embed.add_field(
                        name=f"Match {idx+1}",
                        value=f"**{eq1['nom']}** vs **{eq2['nom']}**",
                        inline=False
                    )
                embed.set_footer(text="RFE | Valorant · BO1 · Bonne chance !")
                await matchs_ch.send(embed=embed)

    print(f"✅ Tirage Swiss ronde {ronde} — {len(matchs)} matchs")

# ── Classement Swiss ────────────────────────
async def mettre_a_jour_classement(tournoi_id: str):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    equipes_snap = db_admin.collection("val_tournois").document(tournoi_id).collection("equipes").get()
    equipes = [{"id": d.id, **d.data()} for d in equipes_snap]
    equipes.sort(key=lambda e: (-e.get("victoires", 0), -e.get("diff_rounds", 0)))

    doc = db_admin.collection("val_tournois").document(tournoi_id).get()
    tournoi = doc.to_dict()
    cat_id = tournoi.get("discord_categorie_id")

    if cat_id:
        categorie = guild.get_channel(int(cat_id))
        if categorie:
            class_ch = discord.utils.get(categorie.channels, name="resultats")
            if class_ch:
                embed = discord.Embed(title="📊 Classement Swiss", color=0xFF4655)
                for i, eq in enumerate(equipes):
                    rang = i + 1
                    emoji = "🥇" if rang == 1 else "🥈" if rang == 2 else "🥉" if rang == 3 else f"{rang}."
                    embed.add_field(
                        name=f"{emoji} {eq['nom']}",
                        value=f"V: {eq.get('victoires',0)} · D: {eq.get('defaites',0)}",
                        inline=True
                    )
                embed.set_footer(text="RFE | Valorant · Mis à jour automatiquement")
                await class_ch.send(embed=embed)

# ── Suppression salons ──────────────────────
async def supprimer_salons_tournoi(tournoi_id: str):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    doc = db_admin.collection("val_tournois").document(tournoi_id).get()
    if not doc.exists:
        return

    data = doc.to_dict()
    cat_id = data.get("discord_categorie_id")
    zone = data.get("zone", "OPEN")

    # Retire les rôles
    roles_retirer = []
    for rid in [ROLE_PARTICIPANT_OPEN_ID, ROLE_PHASE_FINALE_OPEN_ID] + ROLES_POULES_OPEN:
        if rid:
            r = guild.get_role(rid)
            if r:
                roles_retirer.append(r)

    for member in guild.members:
        a_retirer = [r for r in roles_retirer if r in member.roles]
        if a_retirer:
            try:
                await member.remove_roles(*a_retirer)
            except discord.Forbidden:
                pass

    # Supprime salons
    if cat_id:
        categorie = guild.get_channel(int(cat_id))
        if categorie and isinstance(categorie, discord.CategoryChannel):
            for salon in categorie.channels:
                await salon.delete()
            await categorie.delete()

    print(f"✅ Salons Valorant supprimés : {tournoi_id}")

# ── Onboarding ──────────────────────────────
class InscriptionView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Rejoindre RFE Valorant OPEN", style=discord.ButtonStyle.danger, custom_id="btn_valo_open", emoji="🎯")
    async def rejoindre_open(self, interaction: discord.Interaction, button: Button):
        role = interaction.guild.get_role(ROLE_RFE_OPEN_ID)
        if role and role in interaction.user.roles:
            return await interaction.response.send_message("Tu es déjà membre de RFE Valorant OPEN !", ephemeral=True)
        if role:
            await interaction.user.add_roles(role)
        await interaction.response.send_message(
            "✅ **Bienvenue dans RFE Valorant OPEN !**\n\n"
            "Pour participer aux tournois, inscris ton équipe sur **rfe-esport.fr/valorant**\n"
            "Tu recevras le rôle @Participant lors de l'inscription à un tournoi.",
            ephemeral=True
        )

# ── Firestore Listener ──────────────────────
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
                tid = change.document.id
                tournoi = change.document.to_dict()
                if tid in processed or tournoi.get("discord_categorie_id") or tournoi.get("statut") == "termine":
                    continue
                processed.add(tid)
                print(f"🆕 Nouveau tournoi Valorant : {tournoi.get('nom')}")
                db_admin.collection("val_tournois").document(tid).update({"creation_en_cours": True})
                asyncio.run_coroutine_threadsafe(creer_salons_tournoi(tid, tournoi), bot.loop)

            elif change.type.name == "MODIFIED":
                tid = change.document.id
                tournoi = change.document.to_dict()
                if tournoi.get("statut") == "termine" and tournoi.get("discord_categorie_id"):
                    asyncio.run_coroutine_threadsafe(supprimer_salons_tournoi(tid), bot.loop)
                # Tirage Swiss demandé
                if tournoi.get("tirage_ronde") and not tournoi.get(f"tirage_ronde_{tournoi['tirage_ronde']}_fait"):
                    ronde = tournoi["tirage_ronde"]
                    db_admin.collection("val_tournois").document(tid).update({f"tirage_ronde_{ronde}_fait": True})
                    asyncio.run_coroutine_threadsafe(tirage_swiss(tid, ronde), bot.loop)

    db_admin.collection("val_tournois").on_snapshot(on_snapshot)

# ── Commandes ────────────────────────────────
@bot.command(name="setup_inscription")
@commands.has_permissions(administrator=True)
async def setup_inscription(ctx):
    channel = bot.get_channel(CHANNEL_S_INSCRIRE_ID)
    if not channel:
        return await ctx.send("❌ Salon #s-inscrire introuvable")

    embed = discord.Embed(
        title="🎯 RFE | Valorant",
        description=(
            "Bienvenue dans la section Valorant de RFE !\n\n"
            "**RFE OPEN** — Ouvert à tous les niveaux\n"
            "Format Swiss BO1 + Playoffs BO3\n\n"
            "**RFE ASCENDANT** — Bientôt disponible\n\n"
            "Clique ci-dessous pour rejoindre !"
        ),
        color=0xFF4655
    )
    embed.set_footer(text="RFE | Valorant — Powered by RFE Bot")
    await channel.send(embed=embed, view=InscriptionView())
    await ctx.send(f"✅ Message envoyé dans {channel.mention}", delete_after=5)

@bot.command(name="tirage")
@commands.has_permissions(administrator=True)
async def cmd_tirage(ctx, tournoi_id: str, ronde: int):
    await tirage_swiss(tournoi_id, ronde)
    await ctx.send(f"✅ Tirage ronde {ronde} effectué !", delete_after=5)

@bot.command(name="classement_swiss")
@commands.has_permissions(administrator=True)
async def cmd_classement(ctx, tournoi_id: str):
    await mettre_a_jour_classement(tournoi_id)
    await ctx.send("✅ Classement mis à jour !", delete_after=5)

# ── Démarrage ────────────────────────────────
@bot.event
async def on_ready():
    bot.add_view(InscriptionView())
    print(f"✅ RFE Valorant Bot connecté : {bot.user}")
    threading.Thread(target=start_firestore_listener, daemon=True).start()
    print("🔥 Listener Firestore Valorant actif")

bot.run(os.environ["DISCORD_TOKEN"])
