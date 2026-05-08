import discord
import asyncio
import logging
from datetime import datetime
from app.core.settings import settings

logger = logging.getLogger(__name__)

_client: discord.Client | None = None


def _build_embed(signal: dict) -> discord.Embed:
    direction = signal["direction"]
    confidence = signal["confidence"]
    symbol = signal["symbol"]
    tf = signal["timeframe"]

    color = 0x1D9E75 if direction == "LONG" else 0xD85A30

    # Confidence bar  e.g. ████████░░ 82%
    filled = int(confidence / 10)
    bar = "█" * filled + "░" * (10 - filled)

    title = f"{'🟢' if direction == 'LONG' else '🔴'}  {direction}  —  {symbol}/USDT  [{tf}]"

    embed = discord.Embed(title=title, color=color,
                          timestamp=datetime.utcnow())

    entry_low = signal["entry_low"]
    entry_high = signal["entry_high"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    sl = signal["sl"]
    price = signal["entry_price"]
    rr = signal["risk_reward"]

    pct = lambda a, b: f"{((a-b)/b*100):+.2f}%"

    embed.add_field(
        name="Entry zone",
        value=f"`${entry_low:,.4f}` — `${entry_high:,.4f}`",
        inline=False,
    )
    embed.add_field(
        name="🎯 TP1",
        value=f"`${tp1:,.4f}`  ({pct(tp1, price)})",
        inline=True,
    )
    embed.add_field(
        name="🎯 TP2",
        value=f"`${tp2:,.4f}`  ({pct(tp2, price)})",
        inline=True,
    )
    embed.add_field(
        name="🛑 Stop loss",
        value=f"`${sl:,.4f}`  ({pct(sl, price)})",
        inline=True,
    )
    embed.add_field(
        name="Risk / Reward",
        value=f"`1 : {rr}`",
        inline=True,
    )
    embed.add_field(
        name="Confidence",
        value=f"`{bar}` **{confidence:.0f}%**",
        inline=True,
    )

    meta = signal.get("meta", {})
    embed.add_field(
        name="Market context",
        value=(
            f"Fear & Greed: **{meta.get('fear_greed_value', '—')}** "
            f"({meta.get('fear_greed_label', '')})\n"
            f"Funding rate: **{meta.get('funding_rate', 0):+.4f}%**"
        ),
        inline=False,
    )

    # Signal triggers
    triggers = signal.get("triggers", [])
    if triggers:
        bullet_map = {"long": "✅", "short": "✅", "neutral": "⚠️"}
        lines = [
            f"{bullet_map.get(t.get('dir',''), '•')} {t['label']}"
            for t in triggers[:6]
        ]
        embed.add_field(
            name="Signals triggered",
            value="\n".join(lines),
            inline=False,
        )

    rsi = meta.get("rsi")
    vol = meta.get("volume_ratio")
    indicators_txt = ""
    if rsi:
        indicators_txt += f"RSI: **{rsi:.1f}**  "
    if vol:
        indicators_txt += f"Vol ratio: **{vol:.1f}x**  "
    if meta.get("ema_bias"):
        indicators_txt += f"EMA: **{meta['ema_bias']}**"
    if indicators_txt:
        embed.add_field(name="Indicators", value=indicators_txt, inline=False)

    embed.set_footer(text=f"Risk profile: {signal.get('risk_profile','balanced')} · "
                          f"Scores — TA {signal['ta_score']:.0f}  "
                          f"Pattern {signal['pattern_score']:.0f}  "
                          f"ML {signal['ml_score']:.0f}  "
                          f"Context {signal['context_score']:.0f}")
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


async def start_bot():
    """Call once at app startup."""
    if not settings.discord_bot_token:
        logger.warning("DISCORD_BOT_TOKEN not set — Discord disabled")
        return
    await _ensure_client()
