import os
import shutil
import re
import threading
import queue
import time
from fractions import Fraction
import tkinter as tk
import pyautogui
import pytesseract
from PIL import Image, ImageOps, ImageStat

# 1. Localización de Tesseract
for r in [r'C:\Program Files\Tesseract-OCR\tesseract.exe',
          r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
          shutil.which('tesseract')]:
    if r and os.path.isfile(r):
        pytesseract.pytesseract.tesseract_cmd = r
        break

region = None                  # (x, y, ancho, alto) del área elegida
cola = queue.Queue()           # el hilo de OCR deja aquí (operación, resultado)
parar = threading.Event()      # pide al hilo de OCR que termine
pausa = threading.Event()      # activo mientras se está seleccionando el área

MODOS = (0, 1, 2)              # variantes de preprocesado que se prueban en orden
WHITELIST = '0123456789+-*/xX:=?().'


def seleccionar_area():
    pausa.set()  # no capturar mientras la capa gris tapa la pantalla
    sel = tk.Toplevel()
    sel.attributes("-alpha", 0.3, "-fullscreen", True, "-topmost", True)
    sel.config(cursor="cross")
    c = tk.Canvas(sel, bg="gray")
    c.pack(fill="both", expand=True)
    sel.focus_force()

    pos = {}

    def cerrar():
        sel.destroy()
        ventana.after(200, pausa.clear)  # deja que la capa desaparezca antes de capturar

    def press(e):
        pos['x'], pos['y'] = e.x, e.y
        pos['r'] = c.create_rectangle(e.x, e.y, e.x, e.y, outline="#00ff66", width=2)

    def drag(e):
        if 'r' in pos:
            c.coords(pos['r'], pos['x'], pos['y'], e.x, e.y)

    def release(e):
        global region
        if 'x' not in pos:
            cerrar()
            return
        x1, y1 = min(pos['x'], e.x), min(pos['y'], e.y)
        w, h = abs(e.x - pos['x']), abs(e.y - pos['y'])
        cerrar()
        if w > 10 and h > 10:
            region = (x1, y1, w, h)
            btn.config(text="Reajustar Area")
            lbl_op.config(text="Escaneando...")
            lbl_res.config(text="---")

    c.bind("<ButtonPress-1>", press)
    c.bind("<B1-Motion>", drag)
    c.bind("<ButtonRelease-1>", release)
    sel.bind("<Escape>", lambda e: cerrar())  # salida de emergencia


# --- CÁLCULO ---------------------------------------------------------------
NUM = r'\d+(?:\.\d+)?'   # números sin signo (el "-" siempre es un operador)


def formatear(fr):
    if fr.denominator == 1:
        return str(fr.numerator)
    return f"{fr.numerator}/{fr.denominator}"


def operar(a, op, b):
    if op == '+':
        return a + b
    if op == '-':
        return a - b
    if op == '*':
        return a * b
    if op == '/':
        return a / b if b != 0 else None


def despejar(a, op, b, c):
    """Resuelve la incógnita sabiendo que a op b = c. Pasa None en el lado que es '?'."""
    if a is None:                      # ? op b = c
        if op == '+': return c - b
        if op == '-': return c + b
        if op == '*': return c / b if b != 0 else None
        if op == '/': return c * b
    else:                              # a op ? = c
        if op == '+': return c - a
        if op == '-': return a - c
        if op == '*': return c / a if a != 0 else None
        if op == '/': return a / c if c != 0 else None


def normalizar(texto):
    t = texto.replace('\r', '')
    t = re.sub(r'[—–−_]', '-', t)
    t = re.sub(r'[×xX*]', '*', t)
    t = re.sub(r'[÷:/]', '/', t)
    t = t.replace('¿', '?')
    t = re.sub(r'[\[{]', '(', t)
    t = re.sub(r'[\]}]', ')', t)
    return re.sub(r'[^0-9+\-*/?=\n .()]', '', t)


def evaluar(expr):
    """Evalúa una expresión con paréntesis y jerarquía usando Fraction.
    Devuelve (texto, Fraction) o None si no es una expresión válida."""
    expr = re.sub(r'(?<!\d)\.|\.(?!\d)', '', expr)   # puntos sueltos (ruido del OCR)
    tokens = re.findall(rf'{NUM}|[+\-*/()]', expr)
    if not tokens or ''.join(tokens) != re.sub(r'\s', '', expr):
        return None
    if not any(t in ('+', '-', '*', '/') for t in tokens):
        return None

    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def expresion():
        nonlocal pos
        v = termino()
        while peek() in ('+', '-'):
            op = tokens[pos]
            pos += 1
            b = termino()
            v = v + b if op == '+' else v - b
        return v

    def termino():
        nonlocal pos
        v = unario()
        while True:
            p = peek()
            if p in ('*', '/'):
                pos += 1
                b = unario()
                if p == '*':
                    v *= b
                else:
                    if b == 0:
                        raise ZeroDivisionError
                    v /= b
            elif p == '(':          # multiplicación implícita: 2(3+4)
                v *= unario()
            else:
                return v

    def unario():
        nonlocal pos
        p = peek()
        if p == '-':
            pos += 1
            return -unario()
        if p == '+':
            pos += 1
            return unario()
        return atomo()

    def atomo():
        nonlocal pos
        p = peek()
        if p == '(':
            pos += 1
            v = expresion()
            if peek() != ')':
                raise ValueError
            pos += 1
            return v
        if p is None or p in ('+', '-', '*', '/', ')'):
            raise ValueError
        pos += 1
        return Fraction(p)

    try:
        v = expresion()
        if pos != len(tokens):
            return None
    except (ValueError, ZeroDivisionError):
        return None

    texto = ' '.join(tokens)
    texto = re.sub(r'\(\s', '(', texto)
    texto = re.sub(r'\s\)', ')', texto)
    return texto, v


def calcular_vertical(t):
    """
        123
      + 45
      -----
    Primera línea: número. Segunda línea: operador + número. La raya se ignora.
    """
    lineas = [l.strip() for l in t.split('\n')]
    lineas = [l for l in lineas if l and not re.fullmatch(r'[-=.\s]+', l)]
    if len(lineas) < 2:
        return None
    m1 = re.fullmatch(rf'[\s.\-]*({NUM})[\s.\-]*', lineas[0])
    m2 = re.match(rf'([+\-*/])\s*({NUM})', lineas[1])
    if not m1 or not m2:
        return None
    sa, op, sb = m1.group(1), m2.group(1), m2.group(2)
    return f"{sa} {op} {sb}", operar(Fraction(sa), op, Fraction(sb))


def calcular_horizontal(t):
    """? op B = C   |   A op ? = C   (una sola operación con incógnita)"""
    izq, _, der = t.partition('=')
    izq = izq.replace('\n', ' ')
    m = re.search(rf'({NUM}|\?)\s*([+\-*/])\s*({NUM}|\?)', izq)
    if not m:
        return None
    sa, op, sb = m.groups()

    if sa != '?' and sb != '?':
        return f"{sa} {op} {sb}", operar(Fraction(sa), op, Fraction(sb))

    if sa == '?' and sb == '?':
        return None
    mc = re.search(NUM, der)
    if not mc:
        return None
    c = Fraction(mc.group(0))
    if sa == '?':
        res = despejar(None, op, Fraction(sb), c)
    else:
        res = despejar(Fraction(sa), op, None, c)
    return f"{sa} {op} {sb} = {mc.group(0)}", res


def calcular(texto):
    """Devuelve (operación_en_texto, Fraction o None) o None si no reconoce nada."""
    if not texto:
        return None
    t = normalizar(texto)
    r = calcular_vertical(t)
    if r:
        return r
    izq = t.partition('=')[0].replace('\n', ' ')
    if '?' in izq:                       # incógnita: ? + 3 = 5
        return calcular_horizontal(t)
    return evaluar(izq)                  # (2+3)*4, 2+3*4, 10/(5-3), ...
# --- FIN CÁLCULO -----------------------------------------------------------


def preparar(captura, modo=0, invertir=None):
    gris = captura.convert('L')
    # Invertir si el fondo es oscuro (o según lo decidido para toda la captura)
    if invertir is None:
        invertir = ImageStat.Stat(gris).mean[0] < 128
    if invertir:
        gris = ImageOps.invert(gris)
    # Ampliar si el recorte es pequeño (Tesseract lee mejor el texto grande)
    factor = 3 if modo == 2 else (2 if gris.height < 150 else 1)
    if factor > 1:
        gris = gris.resize((gris.width * factor, gris.height * factor), Image.LANCZOS)
    if modo == 0:                        # umbral fijo (el original)
        gris = gris.point(lambda p: 0 if p < 160 else 255)
    else:
        gris = ImageOps.autocontrast(gris, cutoff=2)
        if modo == 1:                    # umbral tras autocontraste
            gris = gris.point(lambda p: 0 if p < 128 else 255)
        # modo 2: escala de grises sin binarizar
    # Margen blanco: Tesseract lee mal el texto pegado al borde
    return ImageOps.expand(gris, border=20, fill=255)


# --- FRACCIONES APILADAS ---------------------------------------------------
#   252
#  ----  - 12 = ?      Se detecta la raya de fracción por geometría y se lee
#   12                  por separado numerador, denominador y el resto.
SIN_FRACCION = object()
WL_FRAC = '0123456789+-*/xX.()'


def _tramos(fila, minimo):
    res, x, n = [], 0, len(fila)
    while x < n:
        if fila[x]:
            s = x
            while x < n and fila[x]:
                x += 1
            if x - s >= minimo:
                res.append((s, x - 1))
        else:
            x += 1
    return res


def detectar_barras(mask, W, H):
    """Busca rayas horizontales finas con un bloque de tinta separado arriba y otro abajo.
    Descarta '-', '=', '÷', la barra del 4, del +, etc."""
    G = max(12, H // 5)                  # hueco máximo entre raya y número
    cands, activos = [], []
    for y in range(H):
        nuevos = []
        for a, b in _tramos(mask[y], 8):
            for c in activos:
                solape = min(b, c['x1']) - max(a, c['x0']) + 1
                if solape >= 0.6 * min(b - a + 1, c['x1'] - c['x0'] + 1):
                    c['y1'] = y
                    c['x0'], c['x1'] = min(c['x0'], a), max(c['x1'], b)
                    nuevos.append(c)
                    break
            else:
                c = {'x0': a, 'x1': b, 'y0': y, 'y1': y}
                cands.append(c)
                nuevos.append(c)
        activos = nuevos

    def hay_tinta(y, xa, xb):
        return any(mask[y][max(0, xa):xb + 1])

    def bloque(y, paso, xa, xb):
        hueco = 0
        while 0 <= y < H and not hay_tinta(y, xa, xb):
            hueco += 1
            y += paso
            if hueco > G:
                return None
        if not (0 <= y < H) or hueco < 1:
            return None
        cerca = lejos = y
        blancos = 0
        while 0 <= y < H:
            if hay_tinta(y, xa, xb):
                lejos, blancos = y, 0
            else:
                blancos += 1
                if blancos >= 2:
                    break
            y += paso
        return cerca, lejos

    barras = []
    for c in cands:
        t = c['y1'] - c['y0'] + 1
        if t > 8:
            continue
        arriba = bloque(c['y0'] - 1, -1, c['x0'], c['x1'])
        abajo = bloque(c['y1'] + 1, 1, c['x0'], c['x1'])
        if not arriba or not abajo:
            continue
        minimo = max(6, 4 * t)
        if abs(arriba[0] - arriba[1]) + 1 < minimo or abs(abajo[0] - abajo[1]) + 1 < minimo:
            continue
        barras.append({'x0': c['x0'], 'x1': c['x1'],
                       'num': (min(arriba), max(arriba)),
                       'den': (min(abajo), max(abajo))})
    barras.sort(key=lambda b: b['x0'])
    return barras


def _caja_tinta(mask, xa, xb, W, H, pad=3):
    if xb < xa:
        return None
    ys = [y for y in range(H) if any(mask[y][xa:xb + 1])]
    if not ys:
        return None
    xs = [x for x in range(xa, xb + 1) if any(mask[y][x] for y in range(ys[0], ys[-1] + 1))]
    return (max(0, xs[0] - pad), max(0, ys[0] - pad), min(W, xs[-1] + 1 + pad), min(H, ys[-1] + 1 + pad))


def _ocr_recorte(captura, caja, modo, invertir, psms, whitelist):
    img = preparar(captura.crop(caja), modo, invertir)
    extra = f' -c tessedit_char_whitelist={WL_FRAC}' if whitelist else ''
    for psm in psms:
        try:
            raw = pytesseract.image_to_string(img, config=f'--psm {psm}{extra}', timeout=3)
        except Exception as e:
            print(f"OCR: {e}")
            continue
        t = normalizar(raw).replace('\n', ' ').strip()
        if t:
            return t
    return ''


def _combinar(partes):
    """partes = [('t', texto), ('f', '(num)/(den)'), ('t', texto), ...]
    Los paréntesis pegados a una fracción son redundantes y el OCR los lee mal:
    si tal cual no cuadra, se prueba quitando los que rodean a la fracción."""
    variantes = [(False, False), (True, True), (True, False), (False, True)]
    for sl, sr in variantes:
        piezas = []
        for i, (tipo, txt) in enumerate(partes):
            if tipo == 't':
                if sl and i + 1 < len(partes):
                    txt = re.sub(r'\(\s*$', '', txt)
                if sr and i > 0:
                    txt = re.sub(r'^\s*\)', '', txt)
            piezas.append(txt)
        izq = normalizar(' '.join(piezas)).partition('=')[0].replace('\n', ' ')
        if '?' in izq:
            return None                  # incógnita + fracción: no soportado
        r = evaluar(izq)
        if r and r[1] is not None:
            return r
    return None


def leer_fracciones(captura, modo):
    """SIN_FRACCION si no hay raya de fracción; si la hay, (texto, valor) o None."""
    gris = captura.convert('L')
    invertir = ImageStat.Stat(gris).mean[0] < 128
    g2 = ImageOps.autocontrast(ImageOps.invert(gris) if invertir else gris, cutoff=2)
    W, H = g2.size
    datos = g2.tobytes()
    mask = [[datos[y * W + x] < 128 for x in range(W)] for y in range(H)]

    barras = detectar_barras(mask, W, H)
    if not barras:
        return SIN_FRACCION
    if modo == 2:
        return None                      # modo poco fiable con dígitos sueltos: mejor no responder que fallar
    for a, b in zip(barras, barras[1:]):
        if b['x0'] <= a['x1']:
            return None                  # fracciones anidadas/solapadas: no soportado

    partes, prev = [], 0
    for b in barras:
        caja = _caja_tinta(mask, prev, b['x0'] - 1, W, H)
        txt = _ocr_recorte(captura, caja, modo, invertir, (7, 6), False) if caja else ''
        partes.append(('t', txt))
        lados = []
        for ya, yb in (b['num'], b['den']):
            caja = (max(0, b['x0'] - 2), max(0, ya - 2), min(W, b['x1'] + 3), min(H, yb + 3))
            lados.append(_ocr_recorte(captura, caja, modo, invertir, (7, 8), modo != 2))
        if not lados[0] or not lados[1]:
            return None
        partes.append(('f', f"({lados[0]})/({lados[1]})"))
        prev = b['x1'] + 1
    caja = _caja_tinta(mask, prev, W - 1, W, H)
    txt = _ocr_recorte(captura, caja, modo, invertir, (7, 6), False) if caja else ''
    partes.append(('t', txt))
    return _combinar(partes)
# --- FIN FRACCIONES --------------------------------------------------------


def leer(captura, modo):
    """psm 6 = bloque de varias líneas (vertical); psm 7 = una sola línea.
    Cualquier error o timeout de Tesseract cuenta como lectura fallida."""
    if ImageStat.Stat(captura.convert('L')).stddev[0] < 4:
        return None                      # recorte prácticamente vacío: no hace falta OCR
    r = leer_fracciones(captura, modo)
    if r is not SIN_FRACCION:            # hay fracción apilada: o se lee bien o se descarta
        return r
    img = preparar(captura, modo)
    extra = '' if modo == 2 else f' -c tessedit_char_whitelist={WHITELIST}'
    for psm in (6, 7):
        try:
            raw = pytesseract.image_to_string(img, config=f'--psm {psm}{extra}', timeout=3)
        except Exception as e:
            print(f"OCR: {e}")
            continue
        r = calcular(raw)
        if r and r[1] is not None:
            return r
    return None


def bucle_fondo():
    ultima_reg = None
    ultimo_hash = None
    ultima_op = ""
    fallo_desde = None
    intento = len(MODOS)     # índice del siguiente preprocesado a probar (len = nada pendiente)

    while not parar.is_set():
        reg = region
        if reg is None or pausa.is_set():
            parar.wait(0.1)
            continue
        try:
            if reg != ultima_reg:   # área nueva: empezar de cero
                ultima_reg, ultimo_hash, ultima_op, fallo_desde = reg, None, "", None
                intento = len(MODOS)

            captura = pyautogui.screenshot(region=reg)
            h = hash(captura.tobytes())

            if h != ultimo_hash:    # pantalla nueva: empezar a leerla desde el primer modo
                ultimo_hash = h
                intento = 0

            if intento < len(MODOS):
                # Un modo por vuelta: si la pantalla cambia, se abandona el reintento
                r = leer(captura, MODOS[intento])
                intento += 1
                if r:
                    intento = len(MODOS)
                    fallo_desde = None
                    op, res = r[0], formatear(r[1])
                    if op != ultima_op:
                        ultima_op = op
                        cola.put((op, res))
                elif fallo_desde is None:
                    fallo_desde = time.monotonic()

            # Si lleva >0.5 s sin leer nada, quita la respuesta vieja para no mostrar un resultado que ya no es
            if fallo_desde and ultima_op and time.monotonic() - fallo_desde > 0.5:
                ultima_op = ""
                cola.put(("...", "---"))

        except Exception as e:
            print(f"Error: {e}")
            parar.wait(0.5)

        parar.wait(0.05)


def revisar_cola():
    """Corre en el hilo de Tkinter: es el único que toca la interfaz."""
    try:
        while True:
            op, res = cola.get_nowait()
            lbl_op.config(text=op)
            lbl_res.config(text=res)
    except queue.Empty:
        pass
    ventana.after(50, revisar_cola)


# Ventana gráfica
ventana = tk.Tk()
ventana.title("FastSolver")
ventana.attributes("-topmost", True)
ventana.geometry("250x135+30+30")
ventana.configure(bg="#111111")

lbl_op = tk.Label(ventana, text="Selecciona el area", font=("Segoe UI", 9), fg="#888888", bg="#111111")
lbl_op.pack(pady=(4, 0))

lbl_res = tk.Label(ventana, text="---", font=("Segoe UI", 26, "bold"), fg="#00ff66", bg="#111111")
lbl_res.pack(expand=True)

btn = tk.Button(ventana, text="🎯 Seleccionar Area", font=("Segoe UI", 9, "bold"),
                bg="#222222", fg="#ffffff", command=seleccionar_area, relief="flat", padx=8, pady=3)
btn.pack(pady=(0, 6))


def al_cerrar():
    parar.set()
    ventana.destroy()


ventana.protocol("WM_DELETE_WINDOW", al_cerrar)

threading.Thread(target=bucle_fondo, daemon=True).start()
revisar_cola()
ventana.mainloop()