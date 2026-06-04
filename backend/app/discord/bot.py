import discord
import asyncio
import logging
import socket
from datetime import datetime, timezone
from app.core.settings import settings

logger = logging.getLogger(__name__)

_client: discord.Client | None = None


_ALERT_LABELS = {
    "new":             ("🆕", "New Signal",       0x1D9E75),
    "upgrade":         ("🔺", "UPGRADE",          0xF5A623),
    "price_deviation": ("📍", "Price Update",     0x5865F2),
    "cooldown_expired":("🔄", "Cooldown Reset",   0x7289DA),
}


def _build_embed(signal: dict) -> discord.Embed:
    direction = signal["direction"]
    confidence = signal["confidence"]
    symbol = signal["symbol"]
    tf = signal["timeframe"]
    alert_type = signal.get("alert_type", "new")

    base_color = 0x1D9E75 if direction == "LONG" else 0xD85A30
    alert_icon, alert_label, alert_color = _ALERT_LABELS.get(
        alert_type, ("🆕", "New Signal", base_color)
    )
    color = alert_color if alert_type in ("upgrade", "price_deviation") else base_color

    dir_icon = "🟢" if direction == "LONG" else "🔴"
    confirmed_tfs = signal.get("confirmed_timeframes")
    tf_label = "+".join(confirmed_tfs) if confirmed_tfs else tf

    if confidence >= 80:
        conf_tier = "ALPHA"
    elif confidence >= 60:
        conf_tier = "PRIME"
    else:
        conf_tier = "SETUP"

    title = (
        f"{alert_icon} {alert_label}  ·  "
        f"{dir_icon} {direction} — {symbol}/USDT  "
        f"[{tf_label} · {conf_tier} {confidence:.0f}%]"
    )

    embed = discord.Embed(title=title, color=color,
                          timestamp=datetime.now(timezone.utc))

    # Non-standard alert context as a compact description line
    _alert_desc = {
        "upgrade":          "*Confidence tier upgraded — setup strengthened.*",
        "price_deviation":  "*Price moved ≥2% from last signal — levels updated.*",
        "cooldown_expired": "*4h cooldown expired — conditions still valid.*",
    }
    if alert_type in _alert_desc:
        embed.description = _alert_desc[alert_type]

    entry_low = signal["entry_low"]
    entry_high = signal["entry_high"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    sl = signal["sl"]
    price = signal["entry_price"]
    leverage = signal.get("leverage") or 1
    tier_num  = signal.get("tier") or 2

    sl_dist  = abs(price - sl)
    tp1_dist = abs(tp1 - price)
    tp2_dist = abs(tp2 - price)
    sl_pct   = sl_dist / price * 100 if price > 0 else 0
    rr_tp1   = round(tp1_dist / sl_dist, 2) if sl_dist > 0 else 0
    rr_tp2   = round(tp2_dist / sl_dist, 2) if sl_dist > 0 else 0

    def _pct(level: float) -> str:
        raw = (level - price) / price * 100
        if leverage > 1:
            return f"({raw:+.2f}%  →  **{raw*leverage:+.1f}%** lev)"
        return f"({raw:+.2f}%)"

    # ── Levels block ─────────────────────────────────────────────────────────
    embed.add_field(
        name="Levels",
        value=(
            f"Entry  `${entry_low:,.4f}` – `${entry_high:,.4f}`\n"
            f"TP1    `${tp1:,.4f}`  {_pct(tp1)}  ·  *50% exit*\n"
            f"TP2    `${tp2:,.4f}`  {_pct(tp2)}  ·  *runner*\n"
            f"SL     `${sl:,.4f}`  (−{sl_pct:.2f}%)"
        ),
        inline=False,
    )

    # ── R/R + Leverage ───────────────────────────────────────────────────────
    embed.add_field(
        name="R/R  ·  Leverage",
        value=f"`1 : {rr_tp1}` (TP1)  ·  `1 : {rr_tp2}` (TP2)  ·  `{leverage}x` Tier {tier_num}",
        inline=False,
    )

    # ── Top 3 triggers ───────────────────────────────────────────────────────
    triggers = signal.get("triggers", [])
    if triggers:
        lines = [f"✅ {t['label']}" for t in triggers[:3]]
        embed.add_field(name="Why this trade", value="\n".join(lines), inline=False)

    tier = signal.get("tier")
    sl_method = signal.get("sl_method", "")
    tier_lbl = f"T{tier}" if tier else ""
    sl_lbl = "surgical SL" if sl_method == "structural_15m" else "ATR SL"
    embed.set_footer(text=(
        f"{tier_lbl} · {sl_lbl} · {signal.get('risk_profile','balanced')} · "
        f"TA {signal['ta_score']:.0f}  "
        f"Pat {signal['pattern_score']:.0f}  "
        f"ML {signal['ml_score']:.0f}  "
        f"Ctx {signal['context_score']:.0f}"
    ))
    return embed


async def send_signal(signal: dict):
    global _client
    if not settings.discord_bot_token or not settings.discord_channel_id:
        logger.warning("Discord not configured — skipping send")
        return
    try:
        if _client is None or _client.is_closed():
            await _ensure_client()
        channel = _client.get_channel(int(settings.discord_channel_id))
        if channel is None:
            channel = await _client.fetch_channel(int(settings.discord_channel_id))
        embed = _build_embed(signal)
        await channel.send(embed=embed)
        logger.info(f"Discord signal sent: {signal['symbol']} {signal['direction']}")
    except Exception as e:
        logger.error(f"Discord send failed: {e}")


async def _ensure_client():
    global _client
    intents = discord.Intents.default()
    _client = discord.Client(intents=intents)

    ready = asyncio.Event()

    @_client.event
    async def on_ready():
        logger.info(f"Discord bot connected as {_client.user}")
        ready.set()

    asyncio.create_task(_client.start(settings.discord_bot_token))
    await asyncio.wait_for(ready.wait(), timeout=30)


async def send_test_message():
    global _client
    if not settings.discord_bot_token or not settings.discord_channel_id:
        raise ValueError("Discord not configured — check DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID")
    if _client is None or _client.is_closed():
        await _ensure_client()
    channel = _client.get_channel(int(settings.discord_channel_id))
    if channel is None:
        channel = await _client.fetch_channel(int(settings.discord_channel_id))
    embed = discord.Embed(
        title="🔔  Test Message — Mhoo Signal Bot",
        description="Discord connection is working correctly.",
        color=0x1D9E75,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Status", value="✅ Connected", inline=True)
    embed.add_field(name="Channel", value=f"<#{settings.discord_channel_id}>", inline=True)
    embed.set_footer(text="Mhoo Signal Bot · test")
    await channel.send(embed=embed)
    logger.info("Discord test message sent")


def _get_local_ip() -> str:
    """Get the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


async def send_server_info():
    """
    Send server IP table to the general channel on every deploy/restart.
    Channel: 1502594092750606418
    """
    _GENERAL_CHANNEL_ID = 1502594092750606418
    global _client
    try:
        if _client is None or _client.is_closed():
            await _ensure_client()

        channel = _client.get_channel(_GENERAL_CHANNEL_ID)
        if channel is None:
            channel = await _client.fetch_channel(_GENERAL_CHANNEL_ID)

        ip = _get_local_ip()
        now = datetime.now(timezone.utc)

        embed = discord.Embed(
            title="🚀  Mhoo Signal Bot — Redeployed",
            description="Server restarted with latest algorithm. All access URLs below.",
            color=0x5865F2,
            timestamp=now,
        )
        embed.add_field(
            name="📡  Access Table",
            value=(
                f"```\n"
                f"{'Service':<22} {'URL'}\n"
                f"{'-'*50}\n"
                f"{'Backend API':<22} http://{ip}:8000\n"
                f"{'Frontend (PC)':<22} http://localhost:3000\n"
                f"{'Frontend (Mobile)':<22} http://{ip}:3000\n"
                f"{'WebSocket':<22} ws://{ip}:8000\n"
                f"{'API Docs':<22} http://{ip}:8000/docs\n"
                f"```"
            ),
            inline=False,
        )
        embed.add_field(name="🖥️  Server IP", value=f"`{ip}`", inline=True)
        embed.add_field(name="🕐  Restarted (UTC)", value=f"`{now.strftime('%Y-%m-%d %H:%M:%S')}`", inline=True)
        embed.add_field(
            name="⚙️  Algorithm  v10  (fixed risk sizing + bug fixes)",
            value=(
                "**Layer 0** Daily macro gate — EMA20/50 + slope + HH/HL structure\n"
                "**Layer 1** 4H HTF — EMA200 + ADX ≥ 12 + ATR-RSI guard\n"
                "**Layer 2** 1H CTF — MACD histogram direction | RSI guard 35/65\n"
                "**Layer 3** 15m/1h entry — BB Squeeze | entry-TF RSI guard 35/65\n"
                "**SMC** Order Blocks + FVGs + Liquidity Sweeps (+0–0.30 boost)\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "**v10 changes over v9:**\n"
                "• Fixed risk sizing — ALPHA 1.5% / PRIME 1.0% / SETUP 0.5% of portfolio\n"
                "  (replaces Fractional Kelly which was producing 5–8% risk per trade)\n"
                "• Risk % configurable per tier in Settings page\n"
                "• Configurable starting balance for real portfolio simulation\n"
                "• SMC: fixed `obs` scope bug — no longer silently returns 0 on error\n"
                "• Equity curve fallback risk uses actual confidence tier, not 1.25%\n"
                "• WebSocket URL auto-upgrades http→ws to prevent connection failures"
            ),
            inline=False,
        )
        embed.set_footer(text="Mhoo Signal Bot · auto deploy notification")

        await channel.send(embed=embed)
        logger.info(f"Server info sent to Discord general channel (IP: {ip})")
    except Exception as e:
        logger.error(f"send_server_info failed: {e}")


async def send_config_change(changes: list[dict]):
    """
    Send config-change notification to the general channel.
    Each item in changes: {"field": str, "old": any, "new": any}
    Channel: 1502594092750606418 (general)
    """
    _CONFIG_CHANNEL_ID = 1502594092750606418
    global _client
    if not settings.discord_bot_token:
        return
    if not changes:
        return
    try:
        if _client is None or _client.is_closed():
            await _ensure_client()

        channel = _client.get_channel(_CONFIG_CHANNEL_ID)
        if channel is None:
            channel = await _client.fetch_channel(_CONFIG_CHANNEL_ID)

        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="⚙️  Config Updated",
            description="Settings were changed via the web dashboard.",
            color=0x5865F2,
            timestamp=now,
        )

        label_map = {
            "watchlist":            "Watchlist",
            "risk_profile":         "Risk Profile",
            "timeframes":           "Timeframes",
            "scan_interval":        "Scan Interval",
            "max_open_positions":    "Max Open Positions",
            "priority_bias":        "Priority Bias",
        }

        for change in changes:
            field = change["field"]
            old_val = change["old"]
            new_val = change["new"]

            if field in ("watchlist", "timeframes"):
                old_str = ", ".join(old_val) if isinstance(old_val, list) else str(old_val)
                new_str = ", ".join(new_val) if isinstance(new_val, list) else str(new_val)
            elif field == "scan_interval":
                old_str = f"{old_val}s ({old_val // 60}m)" if old_val else str(old_val)
                new_str = f"{new_val}s ({new_val // 60}m)"
            else:
                old_str = str(old_val)
                new_str = str(new_val)

            embed.add_field(
                name=label_map.get(field, field),
                value=f"`{old_str}` → `{new_str}`",
                inline=False,
            )

        embed.set_footer(text="Mhoo Signal Bot · config change")
        await channel.send(embed=embed)
        logger.info(f"Config change notification sent: {[c['field'] for c in changes]}")
    except Exception as e:
        logger.error(f"send_config_change failed: {e}")


async def send_outcome_notification(sig, result: str, price: float | None):
    """
    Send WIN / LOSS outcome embed to the signals channel.
    Called by the outcome tracker when a position resolves.
    """
    global _client
    if not settings.discord_bot_token or not settings.discord_channel_id:
        return
    try:
        if _client is None or _client.is_closed():
            await _ensure_client()
        channel = _client.get_channel(int(settings.discord_channel_id))
        if channel is None:
            channel = await _client.fetch_channel(int(settings.discord_channel_id))

        tp1_was_hit = getattr(sig, "tp1_hit", False)
        if result == "win":
            icon, color = "✅", 0x1D9E75
            label = "WIN — TP2 Hit" if tp1_was_hit else "WIN — TP1 Hit"
        elif result == "breakeven":
            icon, color = "🟡", 0xF5A623
            label = "BREAKEVEN — SL at Entry"
        elif result == "expired":
            icon, color = "⏳", 0x6B7280
            label = "EXPIRED — No target hit"
        else:
            icon, color = "❌", 0xD85A30
            label = "LOSS — Stop Hit"

        duration_str = ""
        if sig.result_at and sig.created_at:
            mins = int((sig.result_at - sig.created_at).total_seconds() / 60)
            h, m = divmod(mins, 60)
            duration_str = f"{h}h {m}m" if h else f"{m}m"

        pct_move = ""
        if price is not None and sig.entry_price:
            pct = (price - sig.entry_price) / sig.entry_price * 100
            if sig.direction == "SHORT":
                pct = -pct
            pct_move = f" ({pct:+.2f}%)"

        embed = discord.Embed(
            title=f"{icon} {label}  ·  {sig.direction}  —  {sig.symbol}/USDT  [{sig.timeframe}]",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Entry", value=f"`${sig.entry_price:,.4f}`", inline=True)
        embed.add_field(
            name="Exit price",
            value=f"`${price:,.4f}`{pct_move}" if price else "—",
            inline=True,
        )
        embed.add_field(name="Duration", value=duration_str or "—", inline=True)
        embed.add_field(name="Confidence at entry", value=f"`{sig.confidence:.0f}%`", inline=True)
        embed.add_field(name="Timeframe", value=f"`{sig.timeframe}`", inline=True)
        if tp1_was_hit and result == "win":
            tp1_gain = abs(sig.tp1 - sig.entry_price) / sig.entry_price * 100
            tp2_gain = abs(sig.tp2 - sig.entry_price) / sig.entry_price * 100 if sig.tp2 else None
            embed.add_field(name="TP1 (locked)", value=f"`+{tp1_gain:.2f}%`", inline=True)
            if tp2_gain:
                embed.add_field(name="TP2 gain", value=f"`+{tp2_gain:.2f}%`", inline=True)
        elif result == "loss":
            sl_pct = abs(sig.sl - sig.entry_price) / sig.entry_price * 100
            lev = sig.leverage or 1
            embed.add_field(name="SL distance", value=f"`-{sl_pct:.2f}%`", inline=True)
            if lev > 1:
                embed.add_field(name="Leveraged loss", value=f"`-{sl_pct * lev:.1f}%`", inline=True)
        embed.set_footer(text=f"Mhoo Signal Bot · outcome · signal #{sig.id}")

        await channel.send(embed=embed)
        logger.info(f"Outcome notification sent: {sig.symbol} {sig.direction} {result}")
    except Exception as e:
        logger.error(f"send_outcome_notification failed: {e}")


async def send_tp1_notification(sig, breakeven_sl: float):
    """Send a TP1-hit embed — SL moved to breakeven, riding to TP2."""
    global _client
    if not settings.discord_bot_token or not settings.discord_channel_id:
        return
    try:
        if _client is None or _client.is_closed():
            await _ensure_client()
        channel = _client.get_channel(int(settings.discord_channel_id))
        if channel is None:
            channel = await _client.fetch_channel(int(settings.discord_channel_id))

        tp2_str = f"`${sig.tp2:,.4f}`" if sig.tp2 and sig.tp2 > 0 else "—"
        tp1_gain = abs(sig.tp1 - sig.entry_price) / sig.entry_price * 100

        embed = discord.Embed(
            title=f"🎯 TP1 Hit — Riding to TP2  ·  {sig.direction}  —  {sig.symbol}/USDT  [{sig.timeframe}]",
            color=0xF5A623,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="TP1 hit", value=f"`${sig.tp1:,.4f}` (+{tp1_gain:.2f}%)", inline=True)
        embed.add_field(name="TP2 target", value=tp2_str, inline=True)
        embed.add_field(name="SL → Breakeven", value=f"`${breakeven_sl:,.4f}`", inline=True)
        embed.add_field(name="Entry", value=f"`${sig.entry_price:,.4f}`", inline=True)
        embed.add_field(name="Confidence", value=f"`{sig.confidence:.0f}%`", inline=True)
        embed.set_footer(text=f"Mhoo Signal Bot · tp1-hit · signal #{sig.id}")

        await channel.send(embed=embed)
        logger.info(f"TP1 notification sent: {sig.symbol} {sig.direction}")
    except Exception as e:
        logger.error(f"send_tp1_notification failed: {e}")


async def start_bot():
    """Call once at app startup — starts the Discord client and announces the server IP."""
    if not settings.discord_bot_token:
        logger.warning("DISCORD_BOT_TOKEN not set — Discord disabled")
        return
    await _ensure_client()
    asyncio.create_task(send_server_info())
