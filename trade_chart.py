import io
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

CHART_CANDLE_COUNT = 30


def interval_minutes(interval):
    if interval.endswith("min"):
        return int(
            interval.replace(
                "min",
                "",
            )
        )

    if interval.endswith("h"):
        return (
            int(
                interval.replace(
                    "h",
                    "",
                )
            )
            * 60
        )

    raise ValueError(
        f"Unsupported interval: {interval}"
    )


def format_price(price):
    if price >= 1000:
        return f"{price:.2f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 10:
        return f"{price:.4f}"

    return f"{price:.5f}"


def get_signal_time(trade):
    candle_time = datetime.strptime(
        trade["entry_candle_time"],
        TIME_FORMAT,
    )

    signal_time = (
        candle_time
        + timedelta(
            minutes=interval_minutes(
                trade["interval"]
            )
        )
    )

    return signal_time


def create_trade_chart(
    candles,
    trade,
    symbol,
):
    visible_candles = candles[
        -CHART_CANDLE_COUNT:
    ]

    if len(visible_candles) < 2:
        raise RuntimeError(
            "Not enough candles for chart"
        )

    highs = [
        float(candle["high"])
        for candle in visible_candles
    ]

    lows = [
        float(candle["low"])
        for candle in visible_candles
    ]

    chart_high = max(highs)
    chart_low = min(lows)

    price_range = (
        chart_high - chart_low
    )

    if price_range <= 0:
        price_range = (
            chart_high * 0.001
        )

    entry = float(
        trade["entry"]
    )

    stop_loss = float(
        trade["stop_loss"]
    )

    take_profit = float(
        trade["take_profit"]
    )

    level_high = max(
        chart_high,
        entry,
        stop_loss,
        take_profit,
    )

    level_low = min(
        chart_low,
        entry,
        stop_loss,
        take_profit,
    )

    full_range = (
        level_high - level_low
    )

    if full_range <= 0:
        full_range = price_range

    padding = full_range * 0.10

    figure, axis = plt.subplots(
        figsize=(10, 6),
        dpi=170,
    )

    background = "#0b1020"

    figure.patch.set_facecolor(
        background
    )

    axis.set_facecolor(
        background
    )

    up_color = "#25c98a"
    down_color = "#ef5350"

    minimum_body = (
        price_range * 0.012
    )

    for index, candle in enumerate(
        visible_candles
    ):
        open_price = float(
            candle["open"]
        )

        high_price = float(
            candle["high"]
        )

        low_price = float(
            candle["low"]
        )

        close_price = float(
            candle["close"]
        )

        if close_price >= open_price:
            candle_color = up_color
        else:
            candle_color = down_color

        axis.vlines(
            index,
            low_price,
            high_price,
            color=candle_color,
            linewidth=1.2,
            alpha=0.95,
        )

        body_bottom = min(
            open_price,
            close_price,
        )

        body_height = abs(
            close_price - open_price
        )

        if body_height < minimum_body:
            body_height = minimum_body

            body_bottom = (
                (
                    open_price
                    + close_price
                )
                / 2
                - body_height / 2
            )

        rectangle = Rectangle(
            (
                index - 0.31,
                body_bottom,
            ),
            0.62,
            body_height,
            facecolor=candle_color,
            edgecolor=candle_color,
            linewidth=0.8,
        )

        axis.add_patch(
            rectangle
        )

    axis.axhline(
        entry,
        linewidth=1.5,
        linestyle="-",
        color="#f5c451",
        alpha=0.95,
    )

    axis.axhline(
        stop_loss,
        linewidth=1.5,
        linestyle="--",
        color="#ef5350",
        alpha=0.95,
    )

    axis.axhline(
        take_profit,
        linewidth=1.5,
        linestyle="--",
        color="#25c98a",
        alpha=0.95,
    )

    label_x = (
        len(visible_candles)
        - 0.4
    )

    axis.text(
        label_x,
        entry,
        "  ENTRY "
        + format_price(entry),
        color="#f5c451",
        fontsize=9,
        fontweight="bold",
        va="center",
        ha="left",
    )

    axis.text(
        label_x,
        stop_loss,
        "  SL "
        + format_price(stop_loss),
        color="#ef5350",
        fontsize=9,
        fontweight="bold",
        va="center",
        ha="left",
    )

    axis.text(
        label_x,
        take_profit,
        "  TP "
        + format_price(take_profit),
        color="#25c98a",
        fontsize=9,
        fontweight="bold",
        va="center",
        ha="left",
    )

    signal_time = get_signal_time(
        trade
    )

    title = (
        f"{symbol} · "
        f"{trade['signal']} · "
        f"{trade['interval']}"
    )

    axis.set_title(
        title,
        loc="left",
        color="white",
        fontsize=16,
        fontweight="bold",
        pad=20,
    )

    axis.text(
        0.0,
        1.015,
        "AS  •  FOREX & CRYPTO",
        transform=axis.transAxes,
        color="#f5c451",
        fontsize=9,
        fontweight="bold",
        va="bottom",
    )

    axis.text(
        1.0,
        1.015,
        signal_time.strftime(
            "%Y-%m-%d %H:%M UTC"
        ),
        transform=axis.transAxes,
        color="#aeb7c6",
        fontsize=9,
        va="bottom",
        ha="right",
    )

    tick_step = max(
        len(visible_candles) // 6,
        1,
    )

    tick_positions = list(
        range(
            0,
            len(visible_candles),
            tick_step,
        )
    )

    tick_labels = []

    for position in tick_positions:
        candle_time = datetime.strptime(
            visible_candles[
                position
            ]["datetime"],
            TIME_FORMAT,
        )

        tick_labels.append(
            candle_time.strftime(
                "%H:%M"
            )
        )

    axis.set_xticks(
        tick_positions
    )

    axis.set_xticklabels(
        tick_labels,
        color="#9aa4b5",
        fontsize=8,
    )

    axis.tick_params(
        axis="y",
        colors="#9aa4b5",
        labelsize=8,
    )

    axis.grid(
        True,
        alpha=0.10,
        linewidth=0.6,
    )

    for spine in (
        axis.spines.values()
    ):
        spine.set_color(
            "#252d3d"
        )

    axis.set_xlim(
        -1,
        len(visible_candles) + 5,
    )

    axis.set_ylim(
        level_low - padding,
        level_high + padding,
    )

    axis.set_xlabel(
        "UTC",
        color="#7f8998",
        fontsize=8,
        labelpad=8,
    )

    figure.tight_layout(
        pad=2.2
    )

    image_buffer = io.BytesIO()

    figure.savefig(
        image_buffer,
        format="png",
        dpi=170,
        facecolor=background,
        bbox_inches="tight",
    )

    plt.close(
        figure
    )

    image_buffer.seek(
        0
    )

    return image_buffer
