"""
bot_fichaje.py
==============
Bot de Discord para fichaje automático en Odoo.
Corre 24/7 en Railway o Render (gratis).

Comandos:
    /entrada  → ficha entrada manual
    /salida   → ficha salida manual
    /estado   → muestra el último fichaje
    /ayuda    → muestra los comandos disponibles

Automático:
    09:00 L-V → ficha Franja 1 entrada + salida 14:00
    16:00 L-V → ficha Franja 2 entrada + salida 19:00

Requisitos:
    pip install discord.py python-dotenv xmlrpc apscheduler
"""

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import xmlrpc.client
import datetime
import random
import os
import ssl
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIGURACIÓN ────────────────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")       # token del bot
CANAL_FICHAJE   = int(os.getenv("CANAL_ID", "0"))  # ID del canal donde avisa

ODOO_URL  = os.getenv("ODOO_URL",  "https://alfinfsos.com/")
ODOO_DB   = os.getenv("ODOO_DB",   "casino")
ODOO_USER = os.getenv("ODOO_USER", "natalia.alfaro@alfinf.com")
ODOO_PASS = os.getenv("ODOO_PASS", "1129INGSalud")

# Franjas: (hora_entrada, hora_salida)
FRANJAS = [
    ("09:00", "14:00"),  # Franja 1 - mañana
    ("16:00", "19:00"),  # Franja 2 - tarde
]
# ──────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
scheduler = AsyncIOScheduler(timezone="Europe/Madrid")


# ══════════════════════════════════════════════════════════════
# Helpers Odoo
# ══════════════════════════════════════════════════════════════

def rand_seg():
    return random.randint(0, 300)


def a_utc(dt_local):
    offset = 2 if 3 < dt_local.month < 11 else 1
    return (dt_local - datetime.timedelta(hours=offset)).strftime("%Y-%m-%d %H:%M:%S")


def conectar():
    # Contexto SSL sin verificación (necesario si el servidor Odoo tiene cert autofirmado)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    common = xmlrpc.client.ServerProxy(
        f"{ODOO_URL}/xmlrpc/2/common",
        context=ctx
    )
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
    if not uid:
        raise ValueError("Autenticación fallida en Odoo.")
    models = xmlrpc.client.ServerProxy(
        f"{ODOO_URL}/xmlrpc/2/object",
        context=ctx
    )
    return uid, models


def get_empleado(uid, models):
    ids = models.execute_kw(
        ODOO_DB, uid, ODOO_PASS,
        "hr.employee", "search", [[["user_id", "=", uid]]]
    )
    if not ids:
        raise ValueError("No hay empleado vinculado a este usuario.")
    info = models.execute_kw(
        ODOO_DB, uid, ODOO_PASS,
        "hr.employee", "read", [ids[0]], {"fields": ["name"]}
    )
    return ids[0], info[0]["name"]


def registrar_franja(h_in, h_out, fecha=None):
    """
    Crea un registro hr.attendance con check_in + check_out.
    Devuelve (rid, nombre_empleado, ci_local, co_local).
    """
    if fecha is None:
        fecha = datetime.date.today().strftime("%Y-%m-%d")

    uid, models = conectar()
    emp_id, nombre = get_empleado(uid, models)

    base_in  = datetime.datetime.strptime(f"{fecha} {h_in}",  "%Y-%m-%d %H:%M")
    base_out = datetime.datetime.strptime(f"{fecha} {h_out}", "%Y-%m-%d %H:%M")
    base_in  += datetime.timedelta(seconds=rand_seg())
    base_out += datetime.timedelta(seconds=rand_seg())

    ci = a_utc(base_in)
    co = a_utc(base_out)

    rid = models.execute_kw(
        ODOO_DB, uid, ODOO_PASS,
        "hr.attendance", "create",
        [{"employee_id": emp_id, "check_in": ci, "check_out": co}]
    )
    return rid, nombre, base_in.strftime("%H:%M:%S"), base_out.strftime("%H:%M:%S")


def ultimo_fichaje():
    """Devuelve el último registro de asistencia del empleado."""
    uid, models = conectar()
    emp_id, nombre = get_empleado(uid, models)

    ids = models.execute_kw(
        ODOO_DB, uid, ODOO_PASS,
        "hr.attendance", "search",
        [[["employee_id", "=", emp_id]]],
        {"order": "check_in desc", "limit": 1}
    )
    if not ids:
        return nombre, None, None, None

    rec = models.execute_kw(
        ODOO_DB, uid, ODOO_PASS,
        "hr.attendance", "read",
        [ids[0]], {"fields": ["check_in", "check_out"]}
    )[0]
    return nombre, rec["check_in"], rec["check_out"], ids[0]


# ══════════════════════════════════════════════════════════════
# Función de fichaje automático (scheduler)
# ══════════════════════════════════════════════════════════════

async def fichar_automatico(idx_franja: int):
    """Llamado automáticamente por el scheduler L-V."""
    hoy = datetime.date.today()
    if hoy.weekday() >= 5:
        return  # fin de semana

    canal = bot.get_channel(CANAL_FICHAJE)
    h_in, h_out = FRANJAS[idx_franja]

    try:
        rid, nombre, ci_local, co_local = registrar_franja(h_in, h_out)

        embed = discord.Embed(
            title="✅ Fichaje automático registrado",
            color=0x00e5a0
        )
        embed.add_field(name="👤 Empleado",  value=nombre,              inline=True)
        embed.add_field(name="📅 Fecha",     value=hoy.strftime("%d/%m/%Y"), inline=True)
        embed.add_field(name="🟢 Entrada",   value=ci_local,            inline=True)
        embed.add_field(name="🔴 Salida",    value=co_local,            inline=True)
        embed.add_field(name="🔢 ID Odoo",   value=str(rid),            inline=True)
        embed.set_footer(text=f"Franja {idx_franja+1} · {h_in}→{h_out}")

        if canal:
            await canal.send(embed=embed)

    except Exception as e:
        if canal:
            await canal.send(f"❌ **Error en fichaje automático Franja {idx_franja+1}:** `{e}`")


# ══════════════════════════════════════════════════════════════
# Comandos de Discord
# ══════════════════════════════════════════════════════════════

@bot.tree.command(name="entrada", description="Registra tu entrada ahora mismo en Odoo")
async def cmd_entrada(interaction: discord.Interaction):
    await interaction.response.defer()
    hoy  = datetime.date.today().strftime("%Y-%m-%d")
    hora = datetime.datetime.now().strftime("%H:%M")
    try:
        uid, models = conectar()
        emp_id, nombre = get_empleado(uid, models)
        base = datetime.datetime.now() + datetime.timedelta(seconds=rand_seg())
        ci   = a_utc(base)
        rid  = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS,
            "hr.attendance", "create",
            [{"employee_id": emp_id, "check_in": ci}]
        )
        embed = discord.Embed(title="🟢 Entrada registrada", color=0x00e5a0)
        embed.add_field(name="👤 Empleado", value=nombre, inline=True)
        embed.add_field(name="🕐 Hora",     value=base.strftime("%H:%M:%S"), inline=True)
        embed.add_field(name="🔢 ID Odoo",  value=str(rid), inline=True)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: `{e}`")


@bot.tree.command(name="salida", description="Registra tu salida ahora mismo en Odoo")
async def cmd_salida(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        uid, models = conectar()
        emp_id, nombre = get_empleado(uid, models)

        # Buscar el registro abierto (sin check_out)
        ids = models.execute_kw(
            ODOO_DB, uid, ODOO_PASS,
            "hr.attendance", "search",
            [[["employee_id", "=", emp_id], ["check_out", "=", False]]],
            {"order": "check_in desc", "limit": 1}
        )
        if not ids:
            await interaction.followup.send("⚠️ No hay ninguna entrada abierta sin salida.")
            return

        base = datetime.datetime.now() + datetime.timedelta(seconds=rand_seg())
        co   = a_utc(base)
        models.execute_kw(
            ODOO_DB, uid, ODOO_PASS,
            "hr.attendance", "write",
            [ids, {"check_out": co}]
        )
        embed = discord.Embed(title="🔴 Salida registrada", color=0xff4560)
        embed.add_field(name="👤 Empleado", value=nombre, inline=True)
        embed.add_field(name="🕐 Hora",     value=base.strftime("%H:%M:%S"), inline=True)
        embed.add_field(name="🔢 ID Odoo",  value=str(ids[0]), inline=True)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: `{e}`")


@bot.tree.command(name="estado", description="Muestra tu último fichaje en Odoo")
async def cmd_estado(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        nombre, ci, co, rid = ultimo_fichaje()
        if not ci:
            await interaction.followup.send("ℹ️ No hay fichajes registrados.")
            return
        embed = discord.Embed(title="📋 Último fichaje", color=0x0066ff)
        embed.add_field(name="👤 Empleado", value=nombre,          inline=False)
        embed.add_field(name="🟢 Entrada",  value=ci or "—",       inline=True)
        embed.add_field(name="🔴 Salida",   value=co or "Abierto ⚠️", inline=True)
        embed.add_field(name="🔢 ID",       value=str(rid),        inline=True)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: `{e}`")


@bot.tree.command(name="ayuda", description="Muestra los comandos del bot de fichaje")
async def cmd_ayuda(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = discord.Embed(title="🤖 Bot de Fichaje Odoo", color=0x00e5a0)
    embed.add_field(name="/entrada", value="Registra tu entrada ahora", inline=False)
    embed.add_field(name="/salida",  value="Registra tu salida ahora",  inline=False)
    embed.add_field(name="/estado",  value="Ver tu último fichaje",      inline=False)
    embed.add_field(
        name="⏰ Automático",
        value="09:00 → Franja 1 (09:xx – 14:xx)\n16:00 → Franja 2 (16:xx – 19:xx)\nSolo Lunes a Viernes",
        inline=False
    )
    embed.set_footer(text="Variación aleatoria de 0-5 min aplicada en cada fichaje")
    await interaction.followup.send(embed=embed)


# ══════════════════════════════════════════════════════════════
# Eventos del bot
# ══════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")

    # Sincronizar comandos slash globalmente Y por cada servidor (instantáneo)
    try:
        synced = await bot.tree.sync()
        print(f"Comandos slash globales sincronizados: {len(synced)}")
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"Comandos sincronizados en servidor: {guild.name}")
    except Exception as e:
        print(f"Error sincronizando comandos: {e}")

    # Programar fichajes automáticos L-V
    # Franja 1: dispara a las 09:00 (hora Madrid)
    scheduler.add_job(
        fichar_automatico,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone="Europe/Madrid"),
        args=[0],
        id="franja1"
    )
    # Franja 2: dispara a las 16:00 (hora Madrid)
    scheduler.add_job(
        fichar_automatico,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone="Europe/Madrid"),
        args=[1],
        id="franja2"
    )
    scheduler.start()
    print("Scheduler iniciado: Franja1 09:00 | Franja2 16:00 (L-V, Europa/Madrid)")

    canal = bot.get_channel(CANAL_FICHAJE)
    if canal:
        await canal.send("🤖 **Bot de fichaje iniciado y listo.** Usa `/ayuda` para ver los comandos.")


# ══════════════════════════════════════════════════════════════
# Arranque
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: falta DISCORD_TOKEN en el archivo .env")
        exit(1)
    bot.run(DISCORD_TOKEN)
