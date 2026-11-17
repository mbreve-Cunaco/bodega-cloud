from datetime import datetime
import streamlit as st
import pandas as pd
from db_logica import supabase, validar_login, procesar_pdf_cloud

st.set_page_config(page_title="Bodega Cloud", page_icon="🏭", layout="wide")

# --- CONTROL DE SESIÓN (LOGIN) ---
if "usuario" not in st.session_state:
    st.session_state.usuario = None

if not st.session_state.usuario:
    st.title("🔐 Acceso al Sistema de Bodega")
    with st.form("login_form"):
        user = st.text_input("Usuario")
        pwd = st.text_input("Contraseña", type="password")
        if st.form_submit_button("Ingresar", type="primary"):
            datos_user = validar_login(user, pwd)
            if datos_user:
                st.session_state.usuario = datos_user[0]
                st.rerun()
            else:
                st.error("❌ Usuario o contraseña incorrectos.")
    st.stop()

# --- BARRA SUPERIOR CON DATOS DEL USUARIO ---
u = st.session_state.usuario
col_t, col_b = st.columns([4, 1])
col_t.title(f"🏭 Control de Bodega ({u['nombre']})")
if col_b.button("Cerrar Sesión"):
    st.session_state.usuario = None
    st.rerun()

# --- PESTAÑAS SEGÚN EL ROL (Admin vs Operario) ---
if u["rol"] == "admin":
    tabs = st.tabs(["📊 Inventario", "📥 Ingreso OC (PDF)", "📤 Registrar Salida", "✏️ Editar Nombres"])
else:
    tabs = st.tabs(["📊 Inventario", "📤 Registrar Salida"])

# 1. INVENTARIO GLOBAL
with tabs[0]:
    datos = supabase.table("inventario").select("*").order("id").execute().data
    df = pd.DataFrame(datos)
    if not df.empty:
        df["Estado"] = df.apply(lambda r: "🚨 BAJO STOCK" if float(r["stock"] or 0) <= float(r["limite_minimo"] or 0) else "🟢 OK", axis=1)
        criticos = df[df["Estado"].str.contains("BAJO")]
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Ítems", len(df))
        c2.metric("🚨 Stock Crítico", len(criticos))
        c3.metric("🟢 Stock OK", len(df) - len(criticos))

        if len(criticos) > 0:
            with st.expander("📋 Ver / Descargar Lista de Reposición"):
                rep = "\n".join([f"• {r['nombre_local']} | Cód: {r['codigo_fabrica']} | Stock: {r['stock']}" for _, r in criticos.iterrows()])
                st.code(rep, language="text")
                st.download_button("⬇️ Descargar .txt", rep, file_name="Reposicion.txt")

        buscar = st.text_input("🔍 Buscar repuesto o código:")
        marca_filtro = st.radio("Filtrar por Marca de Máquina:", ["Todas", "Bühler", "IMP.A.C.", "Tecnipes"], horizontal=True)
        if marca_filtro != "Todas":                                        
            df = df[df["maquina"].astype(str).str.contains(marca_filtro, case=False, na=False)]
        if buscar:
            df = df[df.apply(lambda r: r.astype(str).str.contains(buscar, case=False).any(), axis=1)]
            st.dataframe(df[["Estado", "nombre_local", "maquina", "codigo_fabrica", "stock"]], use_container_width=True)

# 2. REGISTRAR SALIDA (Disponible para Admin y Operario)
tab_salida = tabs[2] if u["rol"] == "admin" else tabs[1]
with tab_salida:
    st.subheader("Descontar Material de Bodega")
    datos_s = supabase.table("inventario").select("*").order("nombre_local").execute().data
    opciones = {f"{r['nombre_local']} | Cód: {r['codigo_fabrica']} (Stock: {r['stock']})": r for r in datos_s}
    
    sel = st.selectbox("Selecciona producto:", list(opciones.keys()))
    cant = st.number_input("Cantidad que sale:", min_value=0.5, step=1.0, value=1.0)
    destino = st.text_input("¿Quién retira / Máquina destino?")

    if st.button("Confirmar Salida", type="primary"):
        prod = opciones[sel]
        stk = float(prod["stock"] or 0)
        if cant > stk:
            st.error(f"❌ Stock insuficiente. Disponible: {stk}")
        else:
            nuevo = stk - cant
            hoy = datetime.now().strftime("%d/%m/%Y")
            supabase.table("inventario").update({"stock": nuevo, "ultima_salida": f"-{cant} ({hoy})"}).eq("id", prod["id"]).execute()
            supabase.table("historial_salidas").insert({
                "producto_id": prod["id"], "nombre_local": prod["nombre_local"], "codigo_fabrica": prod["codigo_fabrica"],
                "cantidad": cant, "stock_restante": nuevo, "retira_destino": destino, "usuario_registro": u["username"]
            }).execute()
            st.success(f"✅ Salida registrada. Nuevo stock: {nuevo}")
            st.rerun()

# 3. PESTAÑAS EXCLUSIVAS DEL ADMINISTRADOR
if u["rol"] == "admin":
    with tabs[1]:
        st.subheader("Subir Orden de Compra (PDF)")
        if "up_key" not in st.session_state: st.session_state.up_key = 0
        pdfs = st.file_uploader("Selecciona PDFs", type=["pdf"], accept_multiple_files=True, key=f"up_{st.session_state.up_key}")
        if st.button("Procesar OC", type="primary") and pdfs:
            with st.spinner("Leyendo OC y guardando en la nube..."):
                for p in pdfs:
                    procesar_pdf_cloud(p.getvalue())
            st.session_state.up_key += 1
            st.success("✅ ¡Órdenes guardadas en Supabase!")
            st.rerun()

    with tabs[3]:
        st.subheader("✏️ Editor Rápido de Inventario (Nombres, Códigos y Mínimos)")
        st.caption("Haz doble clic en cualquier celda para editarla y luego presiona Guardar Cambios.")
        df_edit = pd.DataFrame(datos)[["id", "nombre_local", "maquina", "codigo_fabrica", "codigo_referencia", "limite_minimo", "proveedor"]]
        df_modificado = st.data_editor(
            df_edit,
            disabled=["id"],  # Bloqueamos el ID para no romper la base de datos
            use_container_width=True,
            hide_index=True,
            height=450
        )

        if st.button("💾 Guardar Cambios en la Nube", type="primary"):
            with st.spinner("Actualizando base de datos..."):
                for _, fila in df_modificado.iterrows():
                    supabase.table("inventario").update({
                        "nombre_local": str(fila["nombre_local"] or ""),
                        "maquina": str(fila["maquina"] or ""),
                        "codigo_fabrica": str(fila["codigo_fabrica"] or ""),
                        "codigo_referencia": str(fila["codigo_referencia"] or ""),
                        "limite_minimo": float(fila["limite_minimo"] or 1),
                        "proveedor": str(fila["proveedor"] or "")
                    }).eq("id", int(fila["id"])).execute()
            st.success("✅ ¡Todos los cambios se guardaron en Supabase!")
            st.rerun()

