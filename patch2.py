import sys
import re

with open('ai_document.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add schema for block overrides and margin notes
content = content.replace(
    '"page_break_before": {"type": "BOOLEAN"},',
    '"page_break_before": {"type": "BOOLEAN"},\n                        "color": {"type": "STRING", "description": "Hex renk, örn: #FF0000 (Sadece kullanıcı özel renk isterse)"},\n                        "align": {"type": "STRING", "description": "'+"'left'"+', '+"'center'"+', '+"'right'"+' (Sadece kullanıcı isterse)"},\n                        "scale_multiplier": {"type": "NUMBER", "description": "1.0 normal. (Sadece kullanıcı özel boyut isterse)"},\n                        "is_margin_note": {"type": "BOOLEAN", "description": "Sadece kenar boşluğuna küçük bir not düşülecekse true yap."}'
)

# 2. Add pen_dying_effect to page_settings
content = content.replace(
    '"vertical_align": {"type": "STRING", "description": "'+"'top'"+', '+"'center'"+' veya '+"'bottom'"+'"}',
    '"vertical_align": {"type": "STRING", "description": "'+"'top'"+', '+"'center'"+' veya '+"'bottom'"+'"},\n                    "pen_dying_effect": {"type": "BOOLEAN", "description": "Tükenmez kalem bitiyormuş gibi aşağı doğru silikleşsin mi?"}'
)

# 3. Update build_layout for block styles and margin notes
target_build_layout = """        block_type = block["type"]
        style = _style_for_block(block_type, settings)
        scale = style["letter_scale"]"""

replacement_build_layout = """        block_type = block["type"]
        style = _style_for_block(block_type, settings)
        
        # Override with block-specific settings if provided by AI
        if "align" in block:
            style["align"] = str(block["align"])
        if "color" in block:
            style["ink_color"] = str(block["color"])
        
        is_margin_note = block.get("is_margin_note", False)
        if is_margin_note:
            style["letter_scale"] = int(style["letter_scale"] * 0.7)
            style["jitter"] += 3
        elif "scale_multiplier" in block:
            style["letter_scale"] = int(style["letter_scale"] * float(block["scale_multiplier"]))
            
        scale = style["letter_scale"]"""

content = content.replace(target_build_layout, replacement_build_layout)

# 4. Handle horizontal_align and margin_note positioning in build_layout
target_align = """                start_x = settings["margin_left"]
                if settings["horizontal_align"] == "center" or style["align"] == "center":
                    start_x += max(0, (content_width - width) // 2)
                line_counter += 1"""

replacement_align = """                start_x = settings["margin_left"]
                if is_margin_note:
                    import random
                    start_x = PAGE_WIDTH_PX - settings["margin_right"] - 150 + random.randint(0, 50)
                elif settings["horizontal_align"] == "center" or style["align"] == "center":
                    start_x += max(0, (content_width - width) // 2)
                elif settings["horizontal_align"] == "right" or style["align"] == "right":
                    start_x += max(0, content_width - width)
                line_counter += 1"""

content = content.replace(target_align, replacement_align)

# 5. Fix baseline increment for margin note and ink_color
target_line_append = """                    "line_slope": settings["line_slope"],
                    "jitter": style["jitter"],
                    "ink_color": settings["ink_color"],
                    "line_offset_y": 0,
                    "seed": 10_000 + line_counter,
                })
                baseline += line_spacing"""

replacement_line_append = """                    "line_slope": settings["line_slope"] + 15.0 if is_margin_note else settings["line_slope"],
                    "jitter": style["jitter"],
                    "ink_color": style.get("ink_color", settings["ink_color"]),
                    "line_offset_y": 0,
                    "seed": 10_000 + line_counter,
                })
                if not is_margin_note:
                    baseline += line_spacing"""

content = content.replace(target_line_append, replacement_line_append)

# 6. Add pen dying effect at the end of build_layout
target_dying = """    if settings["vertical_align"] == "center":
        for centered_page in pages:
            if not centered_page["lines"]:
                continue
            content_top = min(line["baseline_y"] - line["letter_scale"] for line in centered_page["lines"])
            content_bottom = max(line["baseline_y"] + int(line["letter_scale"] * 0.28) for line in centered_page["lines"])
            safe_top = settings["margin_top"]
            safe_bottom = PAGE_HEIGHT_PX - settings["margin_bottom"]
            desired_center = (safe_top + safe_bottom) / 2
            current_center = (content_top + content_bottom) / 2
            delta = int(round(desired_center - current_center))
            delta = max(safe_top - content_top, min(delta, safe_bottom - content_bottom))
            for line in centered_page["lines"]:
                line["baseline_y"] += delta

    return {"""

replacement_dying = """    if settings["vertical_align"] == "center":
        for centered_page in pages:
            if not centered_page["lines"]:
                continue
            content_top = min(line["baseline_y"] - line["letter_scale"] for line in centered_page["lines"])
            content_bottom = max(line["baseline_y"] + int(line["letter_scale"] * 0.28) for line in centered_page["lines"])
            safe_top = settings["margin_top"]
            safe_bottom = PAGE_HEIGHT_PX - settings["margin_bottom"]
            desired_center = (safe_top + safe_bottom) / 2
            current_center = (content_top + content_bottom) / 2
            delta = int(round(desired_center - current_center))
            delta = max(safe_top - content_top, min(delta, safe_bottom - content_bottom))
            for line in centered_page["lines"]:
                line["baseline_y"] += delta

    if raw_settings.get("pen_dying_effect"):
        total_lines = sum(len(p["lines"]) for p in pages)
        if total_lines > 0:
            global_line_index = 0
            for p in pages:
                for line in p["lines"]:
                    progress = global_line_index / total_lines
                    line["opacity"] = max(0.40, 0.95 - (progress * 0.55))
                    global_line_index += 1

    return {"""

content = content.replace(target_dying, replacement_dying)

# 7. Update Prompt instructions
target_rules = """Kurallar:
- Türkçe yaz; konu gerektiriyorsa yaygın İngilizce terimler kullanılabilir.
- EĞER kullanıcı "bunu tek sayfaya sığdır" veya "1 sayfa olsun" gibi bir talepte bulunduysa:
  * Yazının kelime sayısını kısalt.
  * `page_settings_override` içindeki `letter_height_mm` değerini düşür (örn: 8.0).
  * `page_settings_override` içindeki `line_spacing_mm` değerini düşür (örn: 12.0).
- EĞER kullanıcı sayfa düzeni (kağıt tipi, mürekkep rengi vb.) hakkında seçim yapmadıysa ve sormak istiyorsan, BELGE OLUŞTURMA. Sadece `needs_clarification`: true yap, soruyu sor ve seçenekler sun.
- Çıktı yalnızca tanımlı JSON şemasına uysun."""

replacement_rules = """Kurallar:
- Türkçe yaz; konu gerektiriyorsa yaygın İngilizce terimler kullanılabilir.
- EĞER kullanıcı "bunu tek sayfaya sığdır" veya "1 sayfa olsun" gibi bir talepte bulunduysa:
  * Yazının kelime sayısını kısalt.
  * `page_settings_override` içindeki `letter_height_mm` değerini düşür (örn: 8.0).
  * `page_settings_override` içindeki `line_spacing_mm` değerini düşür (örn: 12.0).
- EĞER kullanıcı yazının çirkin/dağınık/aceleyle yazılmış olmasını istiyorsa:
  * `page_settings_override` içindeki `jitter` değerini artır (örn: 10 veya 15).
  * `line_slope` değerini artır (örn: 7 veya 10).
- EĞER kullanıcı "kalem bitiyormuş gibi olsun", "gittikçe silikleşsin" derse:
  * `page_settings_override` içindeki `pen_dying_effect`: true yap.
- EĞER kullanıcı belirli bir bölümü (başlık vs) Kırmızı yap, Mavi yap, sağa hizala vb. özel bir stil istediyse, O ZAMAN O BLOĞA (blocks içindeki ilgili objeye) `color` (#FF0000), `align` ("center") veya `scale_multiplier` (1.2) ekle. Aksi halde bunları ekleme.
- EĞER kullanıcı önemli yerlerin altını çiz, fosforlu yap veya üstünü karala (strikethrough) gibi şeyler isterse, metnin (`text`) içindeki kelimeleri işaretle: 
  * Fosforlu (sarı) yapmak için kelimeyi `==` içine al (örn: `==önemli==`)
  * Altını kırmızı çizmek için kelimeyi `__` içine al (örn: `__dikkat__`)
  * Üstünü karalamak (hata yapmış gibi) için kelimeyi `~~` içine al (örn: `~~yanlış~~ doğru`)
- EĞER kullanıcı "sayfa kenarına not düş" derse, blocks içerisine `is_margin_note`: true olan yeni bir block ekle.
- EĞER kullanıcı sayfa düzeni hakkında sormak istiyorsan, BELGE OLUŞTURMA. Sadece `needs_clarification`: true yap, soruyu sor ve seçenekler sun.
- Çıktı yalnızca tanımlı JSON şemasına uysun."""

content = content.replace(target_rules, replacement_rules)

with open('ai_document.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("ai_document.py patched")
