import re
from datetime import datetime
import pymupdf
from rapidocr_onnxruntime import RapidOCR
from supabase import create_client

# --- PEGA AQUÍ TUS CREDENCIALES DE SUPABASE ---
SUPABASE_URL = "https://ccpalvvgzcsbeuclwzwe.supabase.co"
SUPABASE_KEY = "sb_publishable_F9LoSXpx1es1XLuP9pIIyg_S2E92wZR"
# ---------------------------------------------

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
ocr = RapidOCR()

def limpiar(texto):
    return re.sub(r'[^A-Z0-9]', '', str(texto).upper())

def validar_login(usuario, clave):
    res = supabase.table("usuarios").select("*").eq("username", usuario).eq("password", clave).execute()
    return res.data if res.data else None

def procesar_pdf_cloud(bytes_pdf):
    doc = pymupdf.open(stream=bytes_pdf, filetype="pdf")
    res, _ = ocr(doc[0].get_pixmap(dpi=250).tobytes("png"))
    if not res: return 0
    
    lineas = [l.strip() for l in res]
    m_oc = re.search(r"ORDEN\s*DE\s*COMPRA\s*N[°º]?\s*([A-Z0-9\-]+)", "\n".join(lineas), re.I)
    num_oc = m_oc.group(1) if m_oc else "S/N"
    
    prov = "Desconocido"
    for i, l in enumerate(lineas):
        if "Señor(es)" in l or "Senor(es)" in l:
            prov = lineas[i+1] if i+1 < len(lineas) else prov
            break

    items, ignorar = [], ["DELIVERY", "INSURANCE", "PACKAGE", "PALLET"]
    i = 0
    while i < len(lineas):
        if lineas[i].replace(".", "").replace(",", "") == "8000":
            cant, desc, nums = 1.0, "", []
            for e in lineas[i+1:i+6]:
                if e.replace(".", "").replace(",", "") == "8000" or "PARA UNA RECEPCION" in e.upper(): break
                if e.replace(",", "").replace(".", "").isdigit(): nums.append(float(e.replace(",", "")))
                elif len(e) > 4 and not desc: desc = e
            if len(nums) >= 2 and nums < 500: cant = nums
            elif len(nums) >= 2 and nums[-2] > 0: cant = float(max(1, round(nums[-1] / nums[-2])))
            if desc and not any(ig in desc.upper() for ig in ignorar):
                items.append({"cant": cant, "desc": desc})
        i += 1

    inv = supabase.table("inventario").select("*").execute().data
    hoy = datetime.now().strftime("%d/%m/%Y")

    for it in items:
        d_limpia, cant, ok = limpiar(it["desc"]), it["cant"], False
        for prod in inv:
            raw_cods = f"{prod.get('codigo_fabrica','')},{prod.get('codigo_referencia','')}"
            cods = [limpiar(c) for c in raw_cods.split(",") if limpiar(c)]
            if any(c in d_limpia for c in cods):
                nuevo_stk = float(prod["stock"] or 0) + cant
                supabase.table("inventario").update({
                    "stock": nuevo_stk,
                    "ultimo_ingreso": f"+{cant} ({hoy})"
                }).eq("id", prod["id"]).execute()
                supabase.table("historial_entradas").insert({
                    "num_oc": num_oc, "proveedor": prov, "codigo_fabrica": it["desc"], "cantidad": cant, "accion": "Sumado"
                }).execute()
                ok = True; break
        
        if not ok:
            # Guarda la descripción larga en codigo_fabrica y deja nombre_local limpio
            supabase.table("inventario").insert({
                "proveedor": prov,
                "nombre_local": "Pendiente de nombrar",
                "maquina": "Pendiente de asignar",
                "especificacion": "Agregado desde OC",
                "stock": cant,
                "ultimo_ingreso": f"+{cant} ({hoy})",
                "limite_minimo": 1,
                "codigo_fabrica": it["desc"]
            }).execute()
            supabase.table("historial_entradas").insert({
                "num_oc": num_oc, "proveedor": prov, "codigo_fabrica": it["desc"], "cantidad": cant, "accion": "Nuevo"
            }).execute()
    return len(items)
