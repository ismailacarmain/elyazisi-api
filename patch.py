import sys
import re

with open('core_generator.py', 'r', encoding='utf-8') as f:
    content = f.read()

pattern = r"            words = text\.split\(' '\)\s+for wi, kelime in enumerate\(words\):\s+if not kelime:\s+x \+= wspc // 2\s+continue\s+for harf in kelime:\s+himg = harf_resmini_al\(harfler, harf, ink, line_opacity, line_kalinlik, rng=line_rng\)\s+if not himg:\s+continue\s+# Gürültü: letter_scale ±%1\*jitter\s+noise = line_rng\.uniform\(-0\.01 \* jitt, 0\.01 \* jitt\)\s+sized = harfi_boyutlandir\(himg, max\(4, int\(lscale \* \(1 \+ noise\)\)\)\)\s+# Hafif açı gürültüsü\s+angle = line_rng\.uniform\(-0\.2 \* jitt, 0\.2 \* jitt\)\s+rot   = sized\.rotate\(angle, resample=Image\.BICUBIC, expand=True\)\s+gw, gh = rot\.size\s+# Taşma koruma — max_x'i geçme\s+if x \+ gw > max_x:\s+break\s+# EXACT Y hesabı:\s+# baseline_y: harfin ALT hizası \(baseline\)\s+# Harfi baseline'a göre hizala\s+slope_dy = \(x - start_x\) \* slope\s+rand_dy  = line_rng\.uniform\(-jitt, jitt\) \* 0\.4\s+final_y  = int\(baseline_y - lscale \+ slope_dy \+ loff \+ rand_dy \+ off_y\)\s+sayfa\.paste\(rot, \(x, final_y\), rot\)\s+x \+= gw \+ lspc \+ line_rng\.randint\(0, 3\)\s+if wi < len\(words\) - 1:\s+x \+= wspc"

replacement = """            words = text.split(' ')
            for wi, kelime in enumerate(words):
                if not kelime:
                    x += wspc // 2
                    continue
                
                is_highlight = False
                is_underline = False
                is_strikethrough = False

                if len(kelime) >= 4:
                    if kelime.startswith('==') and kelime.endswith('=='):
                        is_highlight = True
                        kelime = kelime[2:-2]
                    elif kelime.startswith('__') and kelime.endswith('__'):
                        is_underline = True
                        kelime = kelime[2:-2]
                    elif kelime.startswith('~~') and kelime.endswith('~~'):
                        is_strikethrough = True
                        kelime = kelime[2:-2]

                word_start_x = x

                for harf in kelime:
                    himg = harf_resmini_al(harfler, harf, ink, line_opacity, line_kalinlik, rng=line_rng)
                    if not himg:
                        continue

                    # Gürültü: letter_scale ±%1*jitter
                    noise = line_rng.uniform(-0.01 * jitt, 0.01 * jitt)
                    sized = harfi_boyutlandir(himg, max(4, int(lscale * (1 + noise))))

                    # Hafif açı gürültüsü
                    angle = line_rng.uniform(-0.2 * jitt, 0.2 * jitt)
                    rot   = sized.rotate(angle, resample=Image.BICUBIC, expand=True)
                    gw, gh = rot.size

                    # Taşma koruma — max_x'i geçme
                    if x + gw > max_x:
                        break

                    # EXACT Y hesabı:
                    # baseline_y: harfin ALT hizası (baseline)
                    # Harfi baseline'a göre hizala
                    slope_dy = (x - start_x) * slope
                    rand_dy  = line_rng.uniform(-jitt, jitt) * 0.4
                    final_y  = int(baseline_y - lscale + slope_dy + loff + rand_dy + off_y)

                    sayfa.paste(rot, (x, final_y), rot)
                    x += gw + lspc + line_rng.randint(0, 3)

                word_end_x = x
                
                if is_highlight:
                    try:
                        from PIL import ImageDraw
                        draw = ImageDraw.Draw(sayfa, "RGBA")
                        draw.rectangle([word_start_x, baseline_y - int(lscale * 0.9), word_end_x, baseline_y + int(lscale * 0.15)], fill=(255, 255, 0, 80))
                    except:
                        pass
                
                if is_underline or is_strikethrough:
                    try:
                        from PIL import ImageDraw
                        draw = ImageDraw.Draw(sayfa, "RGBA")
                        line_y = baseline_y + int(lscale * 0.1) if is_underline else baseline_y - int(lscale * 0.45)
                        color = (220, 20, 20, int(line_opacity * 255)) if is_underline else (ink[0], ink[1], ink[2], int(line_opacity * 255))
                        
                        curr_x = word_start_x
                        curr_y = line_y
                        while curr_x < word_end_x:
                            next_x = min(curr_x + line_rng.randint(8, 20), word_end_x)
                            next_y = line_y + line_rng.randint(-3, 3)
                            draw.line([(curr_x, curr_y), (next_x, next_y)], fill=color, width=max(2, int(line_kalinlik) + 2))
                            curr_x = next_x
                            curr_y = next_y
                    except:
                        pass

                if wi < len(words) - 1:
                    x += wspc"""

new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
if new_content != content:
    with open('core_generator.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Patched successfully via regex")
else:
    print("Regex target not found in content!")
