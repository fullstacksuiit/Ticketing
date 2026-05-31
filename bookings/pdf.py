"""Render a confirmed booking to a downloadable PDF e-ticket using reportlab.
Kept dependency-light: standard Helvetica fonts, so we print "Rs." rather than
the ₹ glyph (which the built-in fonts don't carry)."""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BRAND = colors.HexColor("#d32f2f")
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")


def _money(value):
    return f"Rs. {value:,.2f}"


def build_ticket_pdf(booking):
    """Return the e-ticket for `booking` as PDF bytes."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"BusGo ticket {booking.pnr}",
    )

    base = getSampleStyleSheet()
    h_brand = ParagraphStyle(
        "brand", parent=base["Title"], textColor=BRAND, fontSize=22, spaceAfter=2
    )
    label = ParagraphStyle(
        "label", parent=base["Normal"], textColor=MUTED, fontSize=8, leading=10
    )
    value = ParagraphStyle(
        "value", parent=base["Normal"], textColor=INK, fontSize=11, leading=14
    )
    big = ParagraphStyle(
        "big", parent=base["Normal"], textColor=INK, fontSize=14,
        leading=16, fontName="Helvetica-Bold",
    )

    trip = booking.trip
    story = []

    # Header band: brand + PNR
    header = Table(
        [[
            Paragraph("Bus<font color='#facc15'>Go</font>", h_brand),
            Paragraph(
                f"<para align='right'><font color='{MUTED}' size='8'>E-TICKET · PNR</font><br/>"
                f"<font color='{INK}' size='16'><b>{booking.pnr}</b></font></para>",
                value,
            ),
        ]],
        colWidths=[doc.width / 2.0] * 2,
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [header, Spacer(1, 6)]
    story += [_rule(doc.width), Spacer(1, 10)]

    # Operator / Bus
    story += [_two_col(
        doc, "OPERATOR", trip.bus.operator.company_name, "BUS", trip.bus.name, label, value
    ), Spacer(1, 12)]

    # Journey
    board = getattr(booking, "boarding_label", "") or ""
    drop = getattr(booking, "dropping_label", "") or ""
    journey = Table(
        [[
            Paragraph(
                f"{booking.journey_from}<br/>"
                f"<font color='{MUTED}' size='8'>{trip.departure:%d %b %Y, %H:%M}</font>"
                + (f"<br/><font color='{MUTED}' size='8'>Board: {board}</font>" if board else ""),
                big,
            ),
            Paragraph(f"<para align='center'><font color='{MUTED}'>&#8594;</font></para>", value),
            Paragraph(
                f"<para align='right'>{booking.journey_to}<br/>"
                f"<font color='{MUTED}' size='8'>{trip.arrival:%d %b %Y, %H:%M}</font>"
                + (f"<br/><font color='{MUTED}' size='8'>Drop: {drop}</font>" if drop else "")
                + "</para>",
                big,
            ),
        ]],
        colWidths=[doc.width * 0.42, doc.width * 0.16, doc.width * 0.42],
    )
    journey.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.HexColor("#e5e7eb")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#e5e7eb")),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
    ]))
    story += [journey, Spacer(1, 14)]

    # Passengers
    story += [Paragraph("PASSENGERS", label), Spacer(1, 4)]
    rows = [["Seat", "Name", "Age", "Gender", "Fare"]]
    for bs in booking.booked_seats.all():
        rows.append([
            bs.seat.seat_number,
            bs.passenger_name,
            str(bs.passenger_age),
            bs.get_passenger_gender_display(),
            _money(bs.fare),
        ])
    table = Table(rows, colWidths=[
        doc.width * 0.12, doc.width * 0.40, doc.width * 0.12,
        doc.width * 0.18, doc.width * 0.18,
    ])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#e5e7eb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [table, Spacer(1, 12)]

    # Total
    total = Table(
        [["Total paid", _money(booking.total_amount)]],
        colWidths=[doc.width * 0.7, doc.width * 0.3],
    )
    total.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 12),
        ("TEXTCOLOR", (0, 0), (0, 0), MUTED),
        ("TEXTCOLOR", (1, 0), (1, 0), INK),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.HexColor("#e5e7eb")),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
    ]))
    story += [total, Spacer(1, 18)]

    story += [Paragraph(
        f"<font color='{MUTED}' size='8'>Booked via BusGo · Contact {booking.contact_email}"
        + (f" · {booking.contact_phone}" if booking.contact_phone else "")
        + ". Please carry a valid photo ID. Show this PNR while boarding.</font>",
        label,
    )]

    doc.build(story)
    pdf = buf.getvalue()
    buf.close()
    return pdf


def _rule(width):
    t = Table([[""]], colWidths=[width])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1, BRAND)]))
    return t


def _two_col(doc, l1, v1, l2, v2, label, value):
    t = Table(
        [[
            Paragraph(f"<font color='{MUTED}' size='8'>{l1}</font><br/>{v1}", value),
            Paragraph(f"<para align='right'><font color='{MUTED}' size='8'>{l2}</font><br/>{v2}</para>", value),
        ]],
        colWidths=[doc.width / 2.0] * 2,
    )
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t
