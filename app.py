import os
import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
from contextlib import contextmanager
import base64
import hashlib
import secrets
import html
import streamlit.components.v1 as components

# ===================== الثوابت =====================
# مسار قاعدة البيانات: من متغير بيئة (للنشر) أو محلي افتراضياً
DB_NAME = os.environ.get("DB_PATH", "taqyeem_system.db")

# إنشاء مجلد القاعدة إن كان مساره دليلاً غير موجود
_db_dir = os.path.dirname(DB_NAME)
if _db_dir and not os.path.exists(_db_dir):
    os.makedirs(_db_dir, exist_ok=True)

MAX_IMG_SIZE_MB = 5
MAX_LOGIN_ATTEMPTS = 5
MIN_PASSWORD_LEN = 6

# بنود الجولة اليومية للتفقّد
CHECKLIST_ITEMS = [
    "💡 الإنارة",
    "❄️ المكيفات",
    "🚻 دورات المياه",
    "⚡ غرف الكهرباء",
    "🛗 المصاعد",
    "🚰 خزان الصرف الصحي",
    "🌀 مراوح الشفط",
]


class Status:
    PENDING = "قيد الانتظار"
    DONE = "تم الإصلاح"


class Role:
    ADMIN = "مدير"
    TECH = "فني"


class CheckStatus:
    OK = "سليم"
    NEEDS_FIX = "يحتاج صيانة"
    NOT_CHECKED = "—"


# ===================== إعدادات الصفحة =====================
st.set_page_config(
    page_title="نظام إدارة المرافق - تقييم",
    layout="centered",
    initial_sidebar_state="collapsed"
)


# ===================== دوال التشفير =====================

def hash_password(password: str) -> str:
    """تشفير كلمة المرور باستخدام PBKDF2-SHA256."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), salt.encode('utf-8'), 200_000
    )
    return f"pbkdf2${salt}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """التحقق من كلمة المرور المشفرة."""
    if not stored:
        return False
    try:
        scheme, salt, key_hex = stored.split('$')
        if scheme != 'pbkdf2':
            return False
        key = hashlib.pbkdf2_hmac(
            'sha256', password.encode('utf-8'), salt.encode('utf-8'), 200_000
        )
        return secrets.compare_digest(key.hex(), key_hex)
    except (ValueError, AttributeError):
        return False


def is_hashed(password_field: str) -> bool:
    return isinstance(password_field, str) and password_field.startswith("pbkdf2$")


# ===================== دوال مساعدة =====================

@contextmanager
def get_db():
    """مدير سياق آمن لاتصال قاعدة البيانات (commit/rollback تلقائي)."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def safe(text) -> str:
    """ينظّف النص من أي وسوم HTML قبل حقنه في القوالب لمنع XSS."""
    if text is None or text == "":
        return "—"
    try:
        if pd.isna(text):
            return "—"
    except (TypeError, ValueError):
        pass
    return html.escape(str(text))


def file_to_base64(uploaded_file) -> str:
    """يحوّل الملف المرفوع إلى base64 مع التحقق من الحجم."""
    if uploaded_file is None:
        return ""
    data = uploaded_file.getvalue()
    if len(data) > MAX_IMG_SIZE_MB * 1024 * 1024:
        st.warning(f"⚠️ حجم الصورة كبير (أكبر من {MAX_IMG_SIZE_MB} ميجا). تم تجاهلها.")
        return ""
    return base64.b64encode(data).decode()


def render_img(b64: str, style: str, placeholder_html: str = None) -> str:
    """ينشئ وسم <img> من base64 أو عنصر بديل عند غياب الصورة."""
    if b64 and isinstance(b64, str) and b64.strip():
        return f'<img src="data:image/jpeg;base64,{b64}" style="{style}">'
    if placeholder_html is not None:
        return placeholder_html
    return '<div class="photo-placeholder">لا توجد صورة</div>'


def require_admin():
    """يوقف تنفيذ الصفحة إذا لم يكن المستخدم مديراً (تحقق خلفي حقيقي)."""
    if st.session_state.get('user_role') != Role.ADMIN:
        st.error("⛔ هذه الصفحة للمدير فقط.")
        st.stop()


# ===================== تهيئة قاعدة البيانات =====================

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                       username TEXT PRIMARY KEY,
                       password TEXT,
                       role TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS maintenance (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       date TEXT, dept TEXT, office_name TEXT, description TEXT,
                       status TEXT, img_before TEXT, img_after TEXT,
                       action_taken TEXT, tech_name TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS cleaning (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       date TEXT, area TEXT, type TEXT,
                       img_before TEXT, img_after TEXT
                     )''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_checks (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       date TEXT,
                       batch_id TEXT,
                       tech_name TEXT,
                       item TEXT,
                       status TEXT,
                       photo TEXT,
                       notes TEXT
                     )''')

        # ترقية: إضافة عمود tech_name لجدول النظافة إن لم يكن موجوداً
        try:
            c.execute("ALTER TABLE cleaning ADD COLUMN tech_name TEXT")
        except sqlite3.OperationalError:
            pass

        # إنشاء المستخدمين الافتراضيين مع كلمات مرور مشفّرة (مرة واحدة فقط)
        c.execute("SELECT username FROM users WHERE username='admin'")
        if not c.fetchone():
            c.execute(
                "INSERT INTO users VALUES (?,?,?)",
                ('admin', hash_password('admin123'), Role.ADMIN)
            )
        c.execute("SELECT username FROM users WHERE username='tech'")
        if not c.fetchone():
            c.execute(
                "INSERT INTO users VALUES (?,?,?)",
                ('tech', hash_password('tech123'), Role.TECH)
            )


init_db()

# ===================== نظام تسجيل الدخول =====================

if 'login_attempts' not in st.session_state:
    st.session_state.login_attempts = 0

if 'logged_in' not in st.session_state:
    st.markdown(
        "<h2 style='text-align:center;'>🔐 تسجيل دخول النظام / System Login</h2>",
        unsafe_allow_html=True
    )

    if st.session_state.login_attempts >= MAX_LOGIN_ATTEMPTS:
        st.error(f"⛔ تم تجاوز الحد الأقصى للمحاولات ({MAX_LOGIN_ATTEMPTS}). أعد تشغيل التطبيق للمحاولة مجدداً.")
        st.stop()

    u = st.text_input("اسم المستخدم / Username", placeholder="أدخل اسم المستخدم / Enter username")
    p = st.text_input("كلمة المرور / Password", type="password", placeholder="أدخل كلمة المرور / Enter password")

    if st.session_state.login_attempts > 0:
        remaining = MAX_LOGIN_ATTEMPTS - st.session_state.login_attempts
        st.caption(f"🔁 المحاولات المتبقية: {remaining}")

    if st.button("دخول / Login", use_container_width=True):
        ok = False
        role = None
        with get_db() as conn:
            row = conn.execute(
                "SELECT username, password, role FROM users WHERE username=?",
                (u,)
            ).fetchone()
            if row is not None:
                stored = row['password']
                if is_hashed(stored):
                    if verify_password(p, stored):
                        ok = True
                        role = row['role']
                else:
                    # توافق مع البيانات القديمة (نص صريح) — يقبل ثم يُرحّل إلى مشفّر
                    if p == stored:
                        ok = True
                        role = row['role']
                        conn.execute(
                            "UPDATE users SET password=? WHERE username=?",
                            (hash_password(p), u)
                        )

        if ok:
            st.session_state.update({
                'logged_in': True,
                'user_role': role,
                'username': u,
                'login_attempts': 0,
            })
            st.rerun()
        else:
            st.session_state.login_attempts += 1
            st.error("⚠️ بيانات الدخول غير صحيحة")
    st.stop()

# ===================== الشريط الجانبي =====================

st.sidebar.title("🖼️ إعدادات التقرير / Report Settings")

# الشعارات: تُحفظ في الجلسة حتى لا تضيع عند كل rerun
l_logo_file = st.sidebar.file_uploader("شعار اليمين / Right Logo", type=['png', 'jpg', 'jpeg'], key='l_logo')
r_logo_file = st.sidebar.file_uploader("شعار اليسار / Left Logo", type=['png', 'jpg', 'jpeg'], key='r_logo')

if l_logo_file is not None:
    st.session_state.l_logo_b64 = base64.b64encode(l_logo_file.getvalue()).decode()
if r_logo_file is not None:
    st.session_state.r_logo_b64 = base64.b64encode(r_logo_file.getvalue()).decode()

l_logo_b64 = st.session_state.get('l_logo_b64', '')
r_logo_b64 = st.session_state.get('r_logo_b64', '')

org_name      = st.sidebar.text_input("اسم الجهة / المؤسسة", value=" ")
report_footer = st.sidebar.text_input("نص التذييل / Footer Text", value="")

is_admin = st.session_state.user_role == Role.ADMIN

# القائمة حسب الصلاحية
if is_admin:
    menu = [
        "📊 لوحة المؤشرات / Dashboard", "🛠️ الصيانة / Maintenance", "🧹 النظافة / Cleaning",
        "✅ المهام اليومية / Daily Tasks",
        "📋 تقرير بلاغ فردي / Maintenance Report", "🧽 تقرير نظافة فردي / Cleaning Report",
        "🧾 تقرير الجولة اليومية / Daily Inspection Report",
        "📅 التقرير الشهري / Monthly Report", "👥 إدارة المستخدمين / User Management"
    ]
else:
    menu = ["🛠️ الصيانة / Maintenance", "🧹 النظافة / Cleaning", "✅ المهام اليومية / Daily Tasks"]

choice = st.selectbox("انتقل إلى: / Go to:", menu)

# ===================== لوحة المؤشرات (مدير فقط) =====================

if choice == "📊 لوحة المؤشرات / Dashboard":
    require_admin()
    st.header("📊 حالة العمل الحالية")
    with get_db() as conn:
        m_count = conn.execute("SELECT COUNT(*) FROM maintenance").fetchone()[0]
        c_count = conn.execute("SELECT COUNT(*) FROM cleaning").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM maintenance WHERE status=?", (Status.PENDING,)
        ).fetchone()[0]
        done = conn.execute(
            "SELECT COUNT(*) FROM maintenance WHERE status=?", (Status.DONE,)
        ).fetchone()[0]
        dc_batches = conn.execute(
            "SELECT COUNT(DISTINCT batch_id) FROM daily_checks"
        ).fetchone()[0]

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("إجمالي البلاغات", m_count)
    col2.metric("بانتظار الإصلاح", pending)
    col3.metric("تم الإصلاح", done)
    col4.metric("مهام النظافة", c_count)
    col5.metric("الجولات التفقدية", dc_batches)
    st.divider()
    st.info(f"مرحباً بك يا {st.session_state.username} ({st.session_state.user_role})")

# ===================== قسم الصيانة =====================

elif choice == "🛠️ الصيانة / Maintenance":
    st.header("🛠️ إدارة مهام الصيانة")
    t1, t2 = st.tabs(["📝 فتح بلاغ جديد", "🔧 إغلاق بلاغ معلق"])

    with t1:
        with st.form("add_maintenance"):
            dept  = st.selectbox("القسم", ["تكييف", "كهرباء", "سباكة", "نجارة", "أخرى"])
            loc   = st.text_input("الموقع (رقم المكتب / الدور)")
            desc  = st.text_area("وصف المشكلة")
            img_b = st.file_uploader("صورة العطل (قبل)", type=['jpg', 'png', 'jpeg'])
            if st.form_submit_button("إرسال البلاغ", use_container_width=True):
                if loc and desc:
                    with get_db() as conn:
                        conn.execute(
                            "INSERT INTO maintenance (date,dept,office_name,description,status,img_before) "
                            "VALUES (?,?,?,?,?,?)",
                            (
                                datetime.now().strftime("%Y-%m-%d %H:%M"),
                                dept, loc, desc, Status.PENDING, file_to_base64(img_b)
                            )
                        )
                    st.success("✅ تم إرسال البلاغ بنجاح")
                else:
                    st.warning("يرجى تعبئة الموقع والوصف")

    with t2:
        with get_db() as conn:
            pending_tasks = pd.read_sql_query(
                "SELECT id, office_name, description FROM maintenance WHERE status=?",
                conn, params=(Status.PENDING,)
            )
        if not pending_tasks.empty:
            options = {
                f"#{row['id']} - {row['office_name']}": row['id']
                for _, row in pending_tasks.iterrows()
            }
            selected = st.selectbox("اختر البلاغ المراد إغلاقه", list(options.keys()))
            task_id  = options[selected]
            with st.form("close_task"):
                action = st.text_area("الإجراء المتخذ")
                img_a  = st.file_uploader("صورة الإنجاز (بعد)", type=['jpg', 'png', 'jpeg'])
                if st.form_submit_button("إغلاق البلاغ", use_container_width=True):
                    with get_db() as conn:
                        conn.execute(
                            "UPDATE maintenance "
                            "SET status=?, action_taken=?, img_after=?, tech_name=? "
                            "WHERE id=?",
                            (
                                Status.DONE, action, file_to_base64(img_a),
                                st.session_state.username, task_id
                            )
                        )
                    st.success("✅ تم إغلاق البلاغ بنجاح")
                    st.rerun()
        else:
            st.info("لا توجد بلاغات معلقة حالياً.")

# ===================== قسم النظافة =====================

elif choice == "🧹 النظافة / Cleaning":
    st.header("🧹 سجل النظافة اليومي")
    t1, t2 = st.tabs(["📝 إضافة سجل", "📂 عرض السجلات"])

    with t1:
        with st.form("cleaning_form"):
            area    = st.text_input("منطقة التنظيف")
            c_type  = st.selectbox(
                "نوع التنظيف",
                ["يومي روتيني", "تنظيف عميق", "تلميع رخام", "واجهات"]
            )
            c_img_b = st.file_uploader("قبل التنظيف", type=['jpg', 'png', 'jpeg'])
            c_img_a = st.file_uploader("بعد التنظيف",  type=['jpg', 'png', 'jpeg'])
            if st.form_submit_button("حفظ السجل", use_container_width=True):
                if area:
                    with get_db() as conn:
                        conn.execute(
                            "INSERT INTO cleaning (date,area,type,img_before,img_after,tech_name) "
                            "VALUES (?,?,?,?,?,?)",
                            (
                                datetime.now().strftime("%Y-%m-%d %H:%M"),
                                area, c_type,
                                file_to_base64(c_img_b), file_to_base64(c_img_a),
                                st.session_state.username
                            )
                        )
                    st.success("✨ تم الحفظ بنجاح")
                else:
                    st.warning("يرجى إدخال منطقة التنظيف")

    with t2:
        with get_db() as conn:
            df_cl = pd.read_sql_query(
                "SELECT id, date, area, type, tech_name, img_before, img_after "
                "FROM cleaning ORDER BY id DESC", conn
            )
        if df_cl.empty:
            st.info("لا توجد سجلات نظافة حالياً.")
        else:
            display_df = df_cl[['id', 'date', 'area', 'type', 'tech_name']].copy()
            display_df.columns = ['الرقم', 'التاريخ', 'المنطقة', 'النوع', 'المنفذ']
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("🖼️ عرض الصور")
            options = {
                f"#{row['id']} - {row['date']} - {row['area']}": row['id']
                for _, row in df_cl.iterrows()
            }
            picked = st.selectbox("اختر السجل لعرض صوره", list(options.keys()))
            rec_id = options[picked]
            r = df_cl[df_cl['id'] == rec_id].iloc[0]

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**⚠️ قبل التنظيف**")
                if r['img_before']:
                    st.image(base64.b64decode(r['img_before']), use_container_width=True)
                else:
                    st.info("لا توجد صورة")
            with c2:
                st.markdown("**✅ بعد التنظيف**")
                if r['img_after']:
                    st.image(base64.b64decode(r['img_after']), use_container_width=True)
                else:
                    st.info("لا توجد صورة")

            if is_admin:
                st.caption("ℹ️ لإصدار تقرير رسمي مع التوقيع، انتقل إلى \"🧽 تقرير نظافة فردي\".")

# ===================== المهام اليومية (الجولات التفقدية) =====================

elif choice == "✅ المهام اليومية / Daily Tasks":
    st.header("✅ الجولات اليومية للتفقّد")
    t1, t2 = st.tabs(["📋 جولة جديدة", "📂 السجل اليومي"])

    with t1:
        st.info("💡 التقط صورة لكل بند تشيّك عليه — هذا يؤكّد أنك نفّذت المهمة فعلياً.")

        with st.form("daily_inspection_form", clear_on_submit=False):
            general_notes = st.text_area("ملاحظات عامة (اختياري)", key="dc_general_notes")
            st.divider()

            inspection_data = {}
            for idx, item in enumerate(CHECKLIST_ITEMS):
                st.markdown(f"#### {item}")
                col_a, col_b = st.columns([1, 1])
                with col_a:
                    status = st.selectbox(
                        "الحالة",
                        [CheckStatus.NOT_CHECKED, CheckStatus.OK, CheckStatus.NEEDS_FIX],
                        key=f"dc_status_{idx}",
                    )
                    notes = st.text_input("ملاحظات", key=f"dc_notes_{idx}")
                with col_b:
                    photo = st.file_uploader(
                        "📷 صورة التشييك",
                        type=['jpg', 'png', 'jpeg'],
                        key=f"dc_photo_{idx}"
                    )
                inspection_data[item] = {
                    'status': status,
                    'photo': photo,
                    'notes': notes
                }
                st.divider()

            submitted = st.form_submit_button(
                "🚀 حفظ الجولة التفقدية",
                use_container_width=True
            )

            if submitted:
                checked = {k: v for k, v in inspection_data.items()
                           if v['status'] != CheckStatus.NOT_CHECKED}

                if not checked:
                    st.warning("⚠️ لم يتم تشييك أي بند. اختر حالة لبند واحد على الأقل.")
                else:
                    missing_photos = [k for k, v in checked.items() if v['photo'] is None]
                    if missing_photos:
                        st.error(
                            "⚠️ يجب التقاط صورة لكل بند تم تشييكه. "
                            f"الصور الناقصة: {'، '.join(missing_photos)}"
                        )
                    else:
                        batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                        with get_db() as conn:
                            for item, d in checked.items():
                                conn.execute(
                                    "INSERT INTO daily_checks "
                                    "(date, batch_id, tech_name, item, status, photo, notes) "
                                    "VALUES (?,?,?,?,?,?,?)",
                                    (
                                        ts, batch_id,
                                        st.session_state.username,
                                        item, d['status'],
                                        file_to_base64(d['photo']),
                                        d['notes'] or general_notes
                                    )
                                )
                        st.success(
                            f"✅ تم حفظ الجولة التفقدية ({len(checked)} بند). "
                            f"رقم الجولة: DC-{batch_id}"
                        )

    with t2:
        with get_db() as conn:
            batches = pd.read_sql_query(
                "SELECT batch_id, MIN(date) as date, tech_name, COUNT(*) as items_count "
                "FROM daily_checks GROUP BY batch_id ORDER BY batch_id DESC",
                conn
            )

        if batches.empty:
            st.info("لا توجد جولات تفقدية سابقة.")
        else:
            display_b = batches.copy()
            display_b['batch_id'] = "DC-" + display_b['batch_id'].astype(str)
            display_b.columns = ['رقم الجولة', 'التاريخ', 'المنفذ', 'عدد البنود']
            st.dataframe(display_b, use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("🔍 عرض تفاصيل جولة")
            batch_options = {
                f"DC-{row['batch_id']} | {row['date']} | {row['tech_name']}": row['batch_id']
                for _, row in batches.iterrows()
            }
            picked = st.selectbox("اختر الجولة", list(batch_options.keys()))
            batch_id = batch_options[picked]

            with get_db() as conn:
                items_df = pd.read_sql_query(
                    "SELECT * FROM daily_checks WHERE batch_id=? ORDER BY id",
                    conn, params=(batch_id,)
                )

            for _, row in items_df.iterrows():
                with st.container():
                    cols = st.columns([2, 3])
                    with cols[0]:
                        icon = "🟢" if row['status'] == CheckStatus.OK else "🟠"
                        st.markdown(f"### {row['item']}")
                        st.markdown(f"**الحالة:** {icon} {row['status']}")
                        if row['notes']:
                            st.markdown(f"**ملاحظات:** {row['notes']}")
                    with cols[1]:
                        if row['photo']:
                            st.image(base64.b64decode(row['photo']), width=320)
                        else:
                            st.info("لا توجد صورة")
                    st.divider()

# ===================== تقرير بلاغ فردي (مدير فقط) =====================

elif choice == "📋 تقرير بلاغ فردي / Maintenance Report":
    require_admin()
    st.header("📋 تقرير بلاغ فردي / Maintenance Report")
    with get_db() as conn:
        df = pd.read_sql_query("SELECT * FROM maintenance ORDER BY id DESC", conn)

    if df.empty:
        st.info("لا توجد بلاغات مسجلة.")
    else:
        report_id = st.selectbox("اختر رقم البلاغ", df['id'].tolist())
        r = df[df['id'] == report_id].iloc[0]

        img_style    = "width:100%;max-height:220px;object-fit:cover;border-radius:6px;border:1px solid #dde3ec;"
        placeholder  = '<div class="photo-placeholder">لا توجد صورة</div>'

        logo_left_html  = render_img(l_logo_b64, "height:75px;object-fit:contain;",
                                     '<div style="width:75px;"></div>')
        logo_right_html = render_img(r_logo_b64, "height:75px;object-fit:contain;",
                                     '<div style="width:75px;"></div>')
        status_color    = "#27ae60" if r['status'] == Status.DONE else "#e67e22"
        action_html     = safe(r['action_taken']) if r['action_taken'] else 'لم يتم تسجيل إجراء بعد'

        report_html = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Amiri:wght@400;700&family=Cairo:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{font-family:'Cairo',sans-serif;background:#ffffff;color:#1a1a2e;}}
  .page{{max-width:820px;margin:0 auto;background:#ffffff;border:1px solid #dde3ec;box-shadow:0 4px 24px rgba(0,0,0,0.10);}}
  .header{{background:#ffffff;padding:0;display:flex;flex-direction:column;border-bottom:1px solid #dde3ec;}}
  .header-top{{display:flex;align-items:center;justify-content:space-between;padding:20px 28px;gap:16px;}}
  .header-center{{text-align:center;flex:1;}}
  .header-center .org{{font-family:'Amiri',serif;font-size:17px;color:#c9aa5f;letter-spacing:1px;margin-bottom:6px;}}
  .header-center .title{{font-size:23px;font-weight:700;color:#1b3a6b;margin-bottom:6px;}}
  .header-center .ref{{font-size:12px;color:#5a6a82;background:#ffffff;display:inline-block;padding:3px 16px;border-radius:20px;border:1px solid #dde3ec;}}
  .header-stripe{{height:5px;background:linear-gradient(90deg,#1b3a6b,#c9aa5f,#e8c97a,#c9aa5f,#1b3a6b);}}
  .meta-bar{{background:#ffffff;border-bottom:1px solid #dde3ec;padding:10px 28px;display:flex;gap:28px;align-items:center;flex-wrap:wrap;}}
  .meta-item{{font-size:12px;color:#5a6a82;display:flex;align-items:center;gap:5px;}}
  .meta-item strong{{color:#1b3a6b;font-weight:600;}}
  .body{{padding:24px 28px;}}
  .info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:22px;}}
  .info-card{{background:#ffffff;border:1px solid #dde3ec;border-right:4px solid #c9aa5f;border-radius:6px;padding:11px 15px;}}
  .info-card .label{{font-size:10px;color:#8a96aa;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px;}}
  .info-card .value{{font-size:14px;font-weight:700;color:#1b3a6b;}}
  .section-title{{font-size:13px;font-weight:700;color:#1b3a6b;background:#ffffff;border-right:4px solid #c9aa5f;padding:8px 14px;border-radius:4px;margin:18px 0 10px;letter-spacing:.3px;}}
  .detail-box{{background:#ffffff;border:1px solid #dde3ec;border-radius:6px;padding:13px 16px;font-size:14px;line-height:1.9;color:#333d4d;margin-bottom:6px;min-height:48px;}}
  .photos-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:6px;}}
  .photo-box{{text-align:center;}}
  .photo-label{{font-size:11px;font-weight:600;color:#fff;margin-bottom:7px;display:inline-block;padding:3px 14px;border-radius:20px;}}
  .photo-label.before{{background:#e67e22;}}
  .photo-label.after{{background:#27ae60;}}
  .photo-placeholder{{width:100%;height:110px;background:#ffffff;display:flex;align-items:center;justify-content:center;color:#aab;border-radius:6px;border:2px dashed #cdd4e0;font-size:13px;}}
  .status-badge{{display:inline-block;padding:4px 16px;border-radius:20px;font-size:12px;font-weight:700;color:white;background:{status_color};letter-spacing:.3px;}}
  .divider{{border:none;border-top:1px solid #e8ecf2;margin:20px 0;}}
  .footer{{background:#ffffff;border-top:2px solid #dde3ec;padding:20px 28px;display:flex;justify-content:space-between;align-items:flex-end;}}
  .sig-box{{text-align:center;min-width:150px;}}
  .sig-label{{font-size:11px;color:#8a96aa;margin-bottom:22px;font-weight:600;}}
  .sig-line{{border-top:1.5px solid #1b3a6b;width:130px;margin:0 auto 5px;}}
  .sig-name{{font-size:12px;color:#1b3a6b;font-weight:700;}}
  .stamp{{border:2.5px solid #c9aa5f;border-radius:50%;width:88px;height:88px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#c9aa5f;font-size:10px;font-weight:700;text-align:center;transform:rotate(-10deg);line-height:1.5;background:#fff;box-shadow:0 2px 10px rgba(201,170,95,.15);}}
  .confidential{{text-align:center;font-size:10px;color:#aab;padding:8px;border-top:1px solid #e8ecf2;letter-spacing:2px;background:#ffffff;}}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div class="header-top">
      {logo_right_html}
      <div class="header-center">
        <div class="org">{safe(org_name)}</div>
        <div class="title">تقرير صيانة فني</div>
        <div class="ref">الرقم المرجعي: TQ-{r['id']:04d}</div>
      </div>
      {logo_left_html}
    </div>
    <div class="header-stripe"></div>
  </div>
  <div class="meta-bar">
    <div class="meta-item">📅 <strong>تاريخ الإصدار:</strong> {datetime.now().strftime('%Y-%m-%d')}</div>
    <div class="meta-item">👤 <strong>أُعدَّ بواسطة:</strong> {safe(st.session_state.username)}</div>
    <div class="meta-item">📌 <strong>الحالة:</strong> <span class="status-badge">{safe(r['status'])}</span></div>
  </div>
  <div class="body">
    <div class="info-grid">
      <div class="info-card"><div class="label">تاريخ البلاغ</div><div class="value">{safe(r['date'])}</div></div>
      <div class="info-card"><div class="label">القسم الفني</div><div class="value">{safe(r['dept'])}</div></div>
      <div class="info-card"><div class="label">الموقع / رقم المكتب</div><div class="value">{safe(r['office_name'])}</div></div>
      <div class="info-card"><div class="label">الفني المنفذ</div><div class="value">{safe(r['tech_name'])}</div></div>
    </div>
    <hr class="divider">
    <div class="section-title">🔍 وصف المشكلة</div>
    <div class="detail-box">{safe(r['description'])}</div>
    <div class="section-title">✅ الإجراء المتخذ</div>
    <div class="detail-box">{action_html}</div>
    <hr class="divider">
    <div class="section-title">📷 التوثيق المصور</div>
    <div class="photos-grid">
      <div class="photo-box">
        <span class="photo-label before">⚠️ قبل الإصلاح</span><br>
        {render_img(r['img_before'], img_style, placeholder)}
      </div>
      <div class="photo-box">
        <span class="photo-label after">✅ بعد الإصلاح</span><br>
        {render_img(r['img_after'], img_style, placeholder)}
      </div>
    </div>
  </div>
  <div class="footer">
    <div class="sig-box">
      <div class="sig-label">الفني المنفذ</div>
      <div class="sig-line"></div>
      <div class="sig-name">{safe(r['tech_name']) if r['tech_name'] else '———'}</div>
    </div>
    <div class="stamp">تم<br>الاعتماد<br>✦</div>
    <div class="sig-box">
      <div class="sig-label">المشرف المسؤول</div>
      <div class="sig-line"></div>
      <div class="sig-name">———</div>
    </div>
  </div>
  <div class="confidential">{safe(report_footer)}</div>
</div>
</body>
</html>"""

        components.html(report_html, height=960, scrolling=True)
        st.write("")
        st.download_button(
            "📥 تحميل التقرير (HTML)",
            report_html,
            file_name=f"Report_TQ-{report_id:04d}.html",
            mime="text/html",
            use_container_width=True
        )

# ===================== تقرير نظافة فردي (مدير فقط) =====================

elif choice == "🧽 تقرير نظافة فردي / Cleaning Report":
    require_admin()
    st.header("🧽 تقرير نظافة فردي / Cleaning Report")
    with get_db() as conn:
        df = pd.read_sql_query("SELECT * FROM cleaning ORDER BY id DESC", conn)

    if df.empty:
        st.info("لا توجد سجلات نظافة مسجّلة.")
    else:
        options = {
            f"#{row['id']} - {row['date']} - {row['area']}": row['id']
            for _, row in df.iterrows()
        }
        selected = st.selectbox("اختر السجل", list(options.keys()))
        rec_id = options[selected]
        r = df[df['id'] == rec_id].iloc[0]

        img_style   = "width:100%;max-height:240px;object-fit:cover;border-radius:6px;border:1px solid #dde3ec;"
        placeholder = '<div class="photo-placeholder">لا توجد صورة</div>'

        logo_left_html  = render_img(l_logo_b64, "height:75px;object-fit:contain;",
                                     '<div style="width:75px;"></div>')
        logo_right_html = render_img(r_logo_b64, "height:75px;object-fit:contain;",
                                     '<div style="width:75px;"></div>')

        tech_value = r['tech_name'] if 'tech_name' in r.index else None

        clean_html = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Amiri:wght@400;700&family=Cairo:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{font-family:'Cairo',sans-serif;background:#ffffff;color:#1a1a2e;}}
  .page{{max-width:820px;margin:0 auto;background:#ffffff;border:1px solid #dde3ec;box-shadow:0 4px 24px rgba(0,0,0,0.10);}}
  .header{{background:#ffffff;display:flex;flex-direction:column;border-bottom:1px solid #dde3ec;}}
  .header-top{{display:flex;align-items:center;justify-content:space-between;padding:20px 28px;gap:16px;}}
  .header-center{{text-align:center;flex:1;}}
  .header-center .org{{font-family:'Amiri',serif;font-size:17px;color:#c9aa5f;letter-spacing:1px;margin-bottom:6px;}}
  .header-center .title{{font-size:23px;font-weight:700;color:#1b3a6b;margin-bottom:6px;}}
  .header-center .ref{{font-size:12px;color:#5a6a82;background:#ffffff;display:inline-block;padding:3px 16px;border-radius:20px;border:1px solid #dde3ec;}}
  .header-stripe{{height:5px;background:linear-gradient(90deg,#1b3a6b,#c9aa5f,#e8c97a,#c9aa5f,#1b3a6b);}}
  .meta-bar{{background:#ffffff;border-bottom:1px solid #dde3ec;padding:10px 28px;display:flex;gap:28px;align-items:center;flex-wrap:wrap;}}
  .meta-item{{font-size:12px;color:#5a6a82;display:flex;align-items:center;gap:5px;}}
  .meta-item strong{{color:#1b3a6b;font-weight:600;}}
  .body{{padding:24px 28px;}}
  .info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:22px;}}
  .info-card{{background:#ffffff;border:1px solid #dde3ec;border-right:4px solid #c9aa5f;border-radius:6px;padding:11px 15px;}}
  .info-card .label{{font-size:10px;color:#8a96aa;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px;}}
  .info-card .value{{font-size:14px;font-weight:700;color:#1b3a6b;}}
  .section-title{{font-size:13px;font-weight:700;color:#1b3a6b;background:#ffffff;border-right:4px solid #c9aa5f;padding:8px 14px;border-radius:4px;margin:18px 0 10px;}}
  .photos-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;}}
  .photo-box{{text-align:center;}}
  .photo-label{{font-size:11px;font-weight:600;color:#fff;margin-bottom:7px;display:inline-block;padding:3px 14px;border-radius:20px;}}
  .photo-label.before{{background:#e67e22;}}
  .photo-label.after{{background:#27ae60;}}
  .photo-placeholder{{width:100%;height:140px;background:#ffffff;display:flex;align-items:center;justify-content:center;color:#aab;border-radius:6px;border:2px dashed #cdd4e0;font-size:13px;}}
  .divider{{border:none;border-top:1px solid #e8ecf2;margin:20px 0;}}
  .footer{{background:#ffffff;border-top:2px solid #dde3ec;padding:20px 28px;display:flex;justify-content:space-between;align-items:flex-end;}}
  .sig-box{{text-align:center;min-width:150px;}}
  .sig-label{{font-size:11px;color:#8a96aa;margin-bottom:22px;font-weight:600;}}
  .sig-line{{border-top:1.5px solid #1b3a6b;width:130px;margin:0 auto 5px;}}
  .sig-name{{font-size:12px;color:#1b3a6b;font-weight:700;}}
  .stamp{{border:2.5px solid #c9aa5f;border-radius:50%;width:88px;height:88px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#c9aa5f;font-size:10px;font-weight:700;text-align:center;transform:rotate(-10deg);line-height:1.5;background:#fff;box-shadow:0 2px 10px rgba(201,170,95,.15);}}
  .confidential{{text-align:center;font-size:10px;color:#aab;padding:8px;border-top:1px solid #e8ecf2;letter-spacing:2px;background:#ffffff;}}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div class="header-top">
      {logo_right_html}
      <div class="header-center">
        <div class="org">{safe(org_name)}</div>
        <div class="title">تقرير نظافة</div>
        <div class="ref">الرقم المرجعي: CL-{r['id']:04d}</div>
      </div>
      {logo_left_html}
    </div>
    <div class="header-stripe"></div>
  </div>
  <div class="meta-bar">
    <div class="meta-item">📅 <strong>تاريخ الإصدار:</strong> {datetime.now().strftime('%Y-%m-%d')}</div>
    <div class="meta-item">👤 <strong>أُعدَّ بواسطة:</strong> {safe(st.session_state.username)}</div>
  </div>
  <div class="body">
    <div class="info-grid">
      <div class="info-card"><div class="label">تاريخ التنظيف</div><div class="value">{safe(r['date'])}</div></div>
      <div class="info-card"><div class="label">المنطقة</div><div class="value">{safe(r['area'])}</div></div>
      <div class="info-card"><div class="label">نوع التنظيف</div><div class="value">{safe(r['type'])}</div></div>
      <div class="info-card"><div class="label">المنفذ</div><div class="value">{safe(tech_value)}</div></div>
    </div>
    <hr class="divider">
    <div class="section-title">📷 التوثيق المصور</div>
    <div class="photos-grid">
      <div class="photo-box">
        <span class="photo-label before">⚠️ قبل التنظيف</span><br>
        {render_img(r['img_before'], img_style, placeholder)}
      </div>
      <div class="photo-box">
        <span class="photo-label after">✅ بعد التنظيف</span><br>
        {render_img(r['img_after'], img_style, placeholder)}
      </div>
    </div>
  </div>
  <div class="footer">
    <div class="sig-box">
      <div class="sig-label">المنفذ</div>
      <div class="sig-line"></div>
      <div class="sig-name">{safe(tech_value) if tech_value else '———'}</div>
    </div>
    <div class="stamp">تم<br>الاعتماد<br>✦</div>
    <div class="sig-box">
      <div class="sig-label">المشرف المسؤول</div>
      <div class="sig-line"></div>
      <div class="sig-name">———</div>
    </div>
  </div>
  <div class="confidential">{safe(report_footer)}</div>
</div>
</body>
</html>"""

        components.html(clean_html, height=900, scrolling=True)
        st.write("")
        st.download_button(
            "📥 تحميل تقرير النظافة (HTML)",
            clean_html,
            file_name=f"Cleaning_CL-{rec_id:04d}.html",
            mime="text/html",
            use_container_width=True
        )

# ===================== تقرير الجولة اليومية (مدير فقط) =====================

elif choice == "🧾 تقرير الجولة اليومية / Daily Inspection Report":
    require_admin()
    st.header("🧾 تقرير الجولة اليومية / Daily Inspection Report")

    with get_db() as conn:
        batches = pd.read_sql_query(
            "SELECT batch_id, MIN(date) as date, tech_name, COUNT(*) as items_count "
            "FROM daily_checks GROUP BY batch_id ORDER BY batch_id DESC",
            conn
        )

    if batches.empty:
        st.info("لا توجد جولات تفقدية مسجّلة.")
    else:
        batch_options = {
            f"DC-{row['batch_id']} | {row['date']} | {row['tech_name']}": row['batch_id']
            for _, row in batches.iterrows()
        }
        selected = st.selectbox("اختر الجولة", list(batch_options.keys()))
        batch_id = batch_options[selected]

        with get_db() as conn:
            items_df = pd.read_sql_query(
                "SELECT * FROM daily_checks WHERE batch_id=? ORDER BY id",
                conn, params=(batch_id,)
            )

        first_row = items_df.iloc[0]
        report_date = first_row['date']
        tech_name = first_row['tech_name']
        ok_count = len(items_df[items_df['status'] == CheckStatus.OK])
        fix_count = len(items_df[items_df['status'] == CheckStatus.NEEDS_FIX])

        items_html = ""
        for _, row in items_df.iterrows():
            sc = "#27ae60" if row['status'] == CheckStatus.OK else "#e67e22"
            photo_html = render_img(
                row['photo'],
                "width:100%;max-height:200px;object-fit:cover;border-radius:6px;border:1px solid #dde3ec;",
                '<div class="photo-placeholder">لا توجد صورة</div>'
            )
            items_html += f"""
            <div class="check-item">
              <div class="check-header">
                <div class="check-name">{safe(row['item'])}</div>
                <div class="check-status" style="background:{sc};">{safe(row['status'])}</div>
              </div>
              <div class="check-body">
                <div class="check-photo">{photo_html}</div>
                <div class="check-meta">
                  <div class="meta-line"><strong>الوقت:</strong> {safe(row['date'])}</div>
                  <div class="meta-line"><strong>ملاحظات:</strong> {safe(row['notes']) if row['notes'] else 'لا يوجد'}</div>
                </div>
              </div>
            </div>
            """

        logo_left_html  = render_img(l_logo_b64, "height:75px;object-fit:contain;",
                                     '<div style="width:75px;"></div>')
        logo_right_html = render_img(r_logo_b64, "height:75px;object-fit:contain;",
                                     '<div style="width:75px;"></div>')

        daily_html = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Amiri:wght@400;700&family=Cairo:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{font-family:'Cairo',sans-serif;background:#ffffff;color:#1a1a2e;}}
  .page{{max-width:820px;margin:0 auto;background:#ffffff;border:1px solid #dde3ec;box-shadow:0 4px 24px rgba(0,0,0,0.10);}}
  .header{{background:#ffffff;display:flex;flex-direction:column;border-bottom:1px solid #dde3ec;}}
  .header-top{{display:flex;align-items:center;justify-content:space-between;padding:20px 28px;gap:16px;}}
  .header-center{{text-align:center;flex:1;}}
  .header-center .org{{font-family:'Amiri',serif;font-size:17px;color:#c9aa5f;letter-spacing:1px;margin-bottom:6px;}}
  .header-center .title{{font-size:23px;font-weight:700;color:#1b3a6b;margin-bottom:6px;}}
  .header-center .ref{{font-size:12px;color:#5a6a82;background:#ffffff;display:inline-block;padding:3px 16px;border-radius:20px;border:1px solid #dde3ec;}}
  .header-stripe{{height:5px;background:linear-gradient(90deg,#1b3a6b,#c9aa5f,#e8c97a,#c9aa5f,#1b3a6b);}}
  .meta-bar{{background:#ffffff;border-bottom:1px solid #dde3ec;padding:10px 28px;display:flex;gap:28px;align-items:center;flex-wrap:wrap;}}
  .meta-item{{font-size:12px;color:#5a6a82;display:flex;align-items:center;gap:5px;}}
  .meta-item strong{{color:#1b3a6b;font-weight:600;}}
  .body{{padding:24px 28px;}}
  .stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:22px;}}
  .stat-card{{background:#ffffff;border:1px solid #dde3ec;border-top:4px solid #c9aa5f;border-radius:8px;padding:16px;text-align:center;}}
  .stat-card .num{{font-size:32px;font-weight:700;color:#1b3a6b;line-height:1;}}
  .stat-card .num.green{{color:#27ae60;}}
  .stat-card .num.orange{{color:#e67e22;}}
  .stat-card .lbl{{font-size:11px;color:#8a96aa;margin-top:6px;font-weight:600;}}
  .check-item{{border:1px solid #dde3ec;border-radius:8px;margin-bottom:12px;overflow:hidden;border-right:4px solid #c9aa5f;}}
  .check-header{{padding:12px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #dde3ec;background:#ffffff;}}
  .check-name{{font-size:15px;font-weight:700;color:#1b3a6b;}}
  .check-status{{color:#fff;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700;}}
  .check-body{{padding:12px 16px;display:grid;grid-template-columns:1fr 1.5fr;gap:14px;align-items:center;}}
  .check-photo img{{max-width:100%;display:block;}}
  .photo-placeholder{{width:100%;height:120px;background:#ffffff;display:flex;align-items:center;justify-content:center;color:#aab;border-radius:6px;border:2px dashed #cdd4e0;font-size:13px;}}
  .check-meta{{font-size:13px;color:#5a6a82;line-height:1.9;}}
  .check-meta strong{{color:#1b3a6b;}}
  .meta-line{{margin-bottom:4px;}}
  .divider{{border:none;border-top:1px solid #e8ecf2;margin:18px 0;}}
  .footer{{background:#ffffff;border-top:2px solid #dde3ec;padding:20px 28px;display:flex;justify-content:space-between;align-items:flex-end;}}
  .sig-box{{text-align:center;min-width:150px;}}
  .sig-label{{font-size:11px;color:#8a96aa;margin-bottom:22px;font-weight:600;}}
  .sig-line{{border-top:1.5px solid #1b3a6b;width:130px;margin:0 auto 5px;}}
  .sig-name{{font-size:12px;color:#1b3a6b;font-weight:700;}}
  .stamp{{border:2.5px solid #c9aa5f;border-radius:50%;width:88px;height:88px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#c9aa5f;font-size:10px;font-weight:700;text-align:center;transform:rotate(-10deg);line-height:1.5;background:#fff;box-shadow:0 2px 10px rgba(201,170,95,.15);}}
  .confidential{{text-align:center;font-size:10px;color:#aab;padding:8px;border-top:1px solid #e8ecf2;letter-spacing:2px;background:#ffffff;}}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div class="header-top">
      {logo_right_html}
      <div class="header-center">
        <div class="org">{safe(org_name)}</div>
        <div class="title">تقرير الجولة اليومية للتفقّد</div>
        <div class="ref">الرقم المرجعي: DC-{batch_id}</div>
      </div>
      {logo_left_html}
    </div>
    <div class="header-stripe"></div>
  </div>
  <div class="meta-bar">
    <div class="meta-item">📅 <strong>تاريخ الجولة:</strong> {safe(report_date)}</div>
    <div class="meta-item">👤 <strong>الفني المنفّذ:</strong> {safe(tech_name)}</div>
    <div class="meta-item">🕐 <strong>تاريخ الإصدار:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
  </div>
  <div class="body">
    <div class="stats">
      <div class="stat-card"><div class="num">{len(items_df)}</div><div class="lbl">إجمالي البنود</div></div>
      <div class="stat-card"><div class="num green">{ok_count}</div><div class="lbl">سليم</div></div>
      <div class="stat-card"><div class="num orange">{fix_count}</div><div class="lbl">يحتاج صيانة</div></div>
    </div>
    <hr class="divider">
    {items_html}
  </div>
  <div class="footer">
    <div class="sig-box">
      <div class="sig-label">الفني المنفّذ</div>
      <div class="sig-line"></div>
      <div class="sig-name">{safe(tech_name)}</div>
    </div>
    <div class="stamp">تم<br>الاعتماد<br>✦</div>
    <div class="sig-box">
      <div class="sig-label">المشرف المسؤول</div>
      <div class="sig-line"></div>
      <div class="sig-name">———</div>
    </div>
  </div>
  <div class="confidential">{safe(report_footer)}</div>
</div>
</body>
</html>"""

        components.html(daily_html, height=1100, scrolling=True)
        st.write("")
        st.download_button(
            "📥 تحميل التقرير (HTML)",
            daily_html,
            file_name=f"DailyCheck_DC-{batch_id}.html",
            mime="text/html",
            use_container_width=True
        )

# ===================== التقرير الشهري (مدير فقط) =====================

elif choice == "📅 التقرير الشهري / Monthly Report":
    require_admin()
    st.header("📅 التقرير الشهري / Monthly Report")

    col1, col2 = st.columns(2)
    with col1:
        date_from = st.date_input("من تاريخ", value=datetime.now().replace(day=1))
    with col2:
        date_to = st.date_input("إلى تاريخ", value=datetime.now())

    if st.button("🔍 استخراج التقرير", use_container_width=True):
        with get_db() as conn:
            df_m = pd.read_sql_query(
                "SELECT * FROM maintenance WHERE date >= ? AND date <= ? ORDER BY id ASC",
                conn,
                params=(date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d 23:59"))
            )
            df_c = pd.read_sql_query(
                "SELECT * FROM cleaning WHERE date >= ? AND date <= ? ORDER BY id ASC",
                conn,
                params=(date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d 23:59"))
            )
            df_d = pd.read_sql_query(
                "SELECT batch_id, MIN(date) as date, tech_name, COUNT(*) as items_count, "
                "SUM(CASE WHEN status=? THEN 1 ELSE 0 END) as ok_count, "
                "SUM(CASE WHEN status=? THEN 1 ELSE 0 END) as fix_count "
                "FROM daily_checks WHERE date >= ? AND date <= ? "
                "GROUP BY batch_id ORDER BY batch_id ASC",
                conn,
                params=(CheckStatus.OK, CheckStatus.NEEDS_FIX,
                        date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d 23:59"))
            )

        total       = len(df_m)
        done_count  = len(df_m[df_m['status'] == Status.DONE])
        pend_count  = len(df_m[df_m['status'] == Status.PENDING])
        clean_count = len(df_c)
        daily_count = len(df_d)
        pct         = f"{(done_count/total*100):.0f}%" if total > 0 else "—"

        small_img_style = "width:65px;height:50px;object-fit:cover;border-radius:4px;"

        maint_rows = ""
        for _, row in df_m.iterrows():
            sc = "#27ae60" if row['status'] == Status.DONE else "#e67e22"
            ib = render_img(row['img_before'], small_img_style, "—")
            ia = render_img(row['img_after'], small_img_style, "—")
            desc_str = str(row['description']) if row['description'] else ''
            desc_short = desc_str[:45] + ('...' if len(desc_str) > 45 else '')
            maint_rows += f"<tr><td>TQ-{row['id']:04d}</td><td>{safe(row['date'])}</td><td>{safe(row['dept'])}</td><td>{safe(row['office_name'])}</td><td style='text-align:right;'>{safe(desc_short)}</td><td><span style='background:{sc};color:#fff;padding:2px 9px;border-radius:12px;font-size:11px;'>{safe(row['status'])}</span></td><td>{safe(row['tech_name'])}</td><td>{ib}</td><td>{ia}</td></tr>"

        clean_rows = ""
        for _, row in df_c.iterrows():
            cb = render_img(row['img_before'], small_img_style, "—")
            ca = render_img(row['img_after'], small_img_style, "—")
            tech = row['tech_name'] if 'tech_name' in row.index else None
            clean_rows += f"<tr><td>CL-{row['id']:04d}</td><td>{safe(row['date'])}</td><td>{safe(row['area'])}</td><td>{safe(row['type'])}</td><td>{safe(tech)}</td><td>{cb}</td><td>{ca}</td></tr>"

        daily_rows = ""
        for _, row in df_d.iterrows():
            daily_rows += f"<tr><td>DC-{safe(row['batch_id'])}</td><td>{safe(row['date'])}</td><td>{safe(row['tech_name'])}</td><td>{int(row['items_count'])}</td><td><span style='background:#27ae60;color:#fff;padding:2px 9px;border-radius:12px;font-size:11px;'>{int(row['ok_count'])}</span></td><td><span style='background:#e67e22;color:#fff;padding:2px 9px;border-radius:12px;font-size:11px;'>{int(row['fix_count'])}</span></td></tr>"

        logo_left_html  = render_img(l_logo_b64, "height:70px;object-fit:contain;", '<div style="width:70px;"></div>')
        logo_right_html = render_img(r_logo_b64, "height:70px;object-fit:contain;", '<div style="width:70px;"></div>')

        maint_table = (f"<table><thead><tr><th>الرقم</th><th>التاريخ</th><th>القسم</th><th>الموقع</th><th>الوصف</th><th>الحالة</th><th>الفني</th><th>قبل</th><th>بعد</th></tr></thead><tbody>{maint_rows}</tbody></table>" if total > 0 else '<div class="no-data">لا توجد بلاغات في هذه الفترة</div>')
        clean_table = (f"<table><thead><tr><th>الرقم</th><th>التاريخ</th><th>المنطقة</th><th>نوع التنظيف</th><th>المنفذ</th><th>قبل</th><th>بعد</th></tr></thead><tbody>{clean_rows}</tbody></table>" if clean_count > 0 else '<div class="no-data">لا توجد مهام نظافة في هذه الفترة</div>')
        daily_table = (f"<table><thead><tr><th>الرقم</th><th>التاريخ</th><th>المنفذ</th><th>عدد البنود</th><th>سليم</th><th>يحتاج صيانة</th></tr></thead><tbody>{daily_rows}</tbody></table>" if daily_count > 0 else '<div class="no-data">لا توجد جولات تفقدية في هذه الفترة</div>')

        monthly_html = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar"><head><meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Amiri:wght@400;700&family=Cairo:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:'Cairo',sans-serif;background:#ffffff;color:#1a1a2e;}}
.page{{max-width:980px;margin:0 auto;background:#ffffff;border:1px solid #dde3ec;box-shadow:0 4px 24px rgba(0,0,0,0.10);}}
.header{{background:#ffffff;display:flex;flex-direction:column;border-bottom:1px solid #dde3ec;}}
.header-top{{display:flex;align-items:center;justify-content:space-between;padding:20px 28px;gap:16px;}}
.header-center{{text-align:center;flex:1;}}
.header-center .org{{font-family:'Amiri',serif;font-size:17px;color:#c9aa5f;letter-spacing:1px;margin-bottom:6px;}}
.header-center .title{{font-size:22px;font-weight:700;color:#1b3a6b;margin-bottom:6px;}}
.header-center .period{{font-size:12px;color:#5a6a82;background:#ffffff;display:inline-block;padding:3px 16px;border-radius:20px;border:1px solid #dde3ec;}}
.header-stripe{{height:5px;background:linear-gradient(90deg,#1b3a6b,#c9aa5f,#e8c97a,#c9aa5f,#1b3a6b);}}
.meta-bar{{background:#ffffff;border-bottom:1px solid #dde3ec;padding:10px 28px;display:flex;gap:28px;flex-wrap:wrap;}}
.meta-item{{font-size:12px;color:#5a6a82;}}
.meta-item strong{{color:#1b3a6b;font-weight:600;}}
.body{{padding:24px 28px;}}
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:26px;}}
.stat-card{{background:#ffffff;border:1px solid #dde3ec;border-top:4px solid #c9aa5f;border-radius:8px;padding:14px 8px;text-align:center;}}
.stat-card .num{{font-size:32px;font-weight:700;color:#1b3a6b;line-height:1;}}
.stat-card .num.green{{color:#27ae60;}}
.stat-card .num.orange{{color:#e67e22;}}
.stat-card .num.blue{{color:#2980b9;}}
.stat-card .num.purple{{color:#8e44ad;}}
.stat-card .lbl{{font-size:10px;color:#8a96aa;margin-top:6px;font-weight:600;}}
.section-title{{font-size:13px;font-weight:700;color:#1b3a6b;background:#ffffff;border-right:4px solid #c9aa5f;padding:8px 14px;border-radius:4px;margin:22px 0 12px;}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;}}
th{{background:#1b3a6b;color:#ffffff;padding:10px 8px;text-align:center;border:1px solid #1b3a6b;}}
td{{padding:9px 8px;text-align:center;border:1px solid #e8ecf2;color:#333d4d;}}
.divider{{border:none;border-top:1px solid #e8ecf2;margin:22px 0;}}
.footer{{background:#ffffff;border-top:2px solid #dde3ec;padding:18px 28px;display:flex;justify-content:space-between;align-items:center;}}
.footer-info{{font-size:12px;color:#5a6a82;line-height:1.9;}}
.footer-info strong{{color:#1b3a6b;}}
.stamp{{border:2.5px solid #c9aa5f;border-radius:50%;width:82px;height:82px;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#c9aa5f;font-size:10px;font-weight:700;text-align:center;transform:rotate(-10deg);background:#fff;}}
.confidential{{text-align:center;font-size:10px;color:#aab;padding:8px;border-top:1px solid #e8ecf2;letter-spacing:2px;background:#ffffff;}}
.no-data{{text-align:center;color:#aab;padding:22px;font-style:italic;border:1px dashed #dde3ec;border-radius:6px;background:#ffffff;}}
</style></head>
<body><div class="page">
<div class="header"><div class="header-top">{logo_right_html}<div class="header-center"><div class="org">{safe(org_name)}</div><div class="title">التقرير الشهري للمرافق</div><div class="period">الفترة: {date_from.strftime('%Y-%m-%d')} — {date_to.strftime('%Y-%m-%d')}</div></div>{logo_left_html}</div><div class="header-stripe"></div></div>
<div class="meta-bar"><div class="meta-item">📅 <strong>تاريخ الإصدار:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</div><div class="meta-item">👤 <strong>أُعدَّ بواسطة:</strong> {safe(st.session_state.username)}</div><div class="meta-item">📊 <strong>نسبة الإنجاز:</strong> {pct}</div></div>
<div class="body">
<div class="stats">
<div class="stat-card"><div class="num">{total}</div><div class="lbl">إجمالي البلاغات</div></div>
<div class="stat-card"><div class="num green">{done_count}</div><div class="lbl">تم الإصلاح</div></div>
<div class="stat-card"><div class="num orange">{pend_count}</div><div class="lbl">قيد الانتظار</div></div>
<div class="stat-card"><div class="num blue">{clean_count}</div><div class="lbl">مهام النظافة</div></div>
<div class="stat-card"><div class="num purple">{daily_count}</div><div class="lbl">الجولات التفقدية</div></div>
</div>
<hr class="divider"><div class="section-title">🛠️ جدول بلاغات الصيانة</div>{maint_table}
<hr class="divider"><div class="section-title">🧹 جدول مهام النظافة</div>{clean_table}
<hr class="divider"><div class="section-title">✅ جدول الجولات اليومية للتفقّد</div>{daily_table}
</div>
<div class="footer"><div class="footer-info"><strong>تاريخ الإصدار:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}<br><strong>أُعدَّ بواسطة:</strong> {safe(st.session_state.username)}</div><div class="stamp">تم<br>الاعتماد<br>✦</div><div class="footer-info" style="text-align:left;"><strong>نسبة الإنجاز:</strong> {pct}<br><strong>إجمالي البلاغات:</strong> {total}</div></div>
<div class="confidential">{safe(report_footer)}</div>
</div></body></html>"""

        components.html(monthly_html, height=1100, scrolling=True)
        st.write("")
        st.download_button(
            "📥 تحميل التقرير الشهري (HTML)",
            monthly_html,
            file_name=f"Monthly_{date_from}_{date_to}.html",
            mime="text/html",
            use_container_width=True
        )

# ===================== إدارة المستخدمين (مدير فقط) =====================

elif choice == "👥 إدارة المستخدمين / User Management":
    require_admin()
    st.header("👥 إدارة المستخدمين / User Management")

    with get_db() as conn:
        users_df = pd.read_sql_query("SELECT username, role FROM users", conn)

    st.subheader("المستخدمون الحاليون")
    st.dataframe(users_df, use_container_width=True, hide_index=True)

    st.divider()

    st.subheader("➕ إضافة مستخدم جديد")
    with st.form("add_user_form"):
        new_user = st.text_input("اسم المستخدم / Username")
        new_pass = st.text_input("كلمة المرور / Password", type="password")
        new_role = st.selectbox("الصلاحية", [Role.TECH, Role.ADMIN])
        if st.form_submit_button("إضافة", use_container_width=True):
            if not new_user or not new_pass:
                st.warning("يرجى تعبئة جميع الحقول")
            elif len(new_pass) < MIN_PASSWORD_LEN:
                st.warning(f"⚠️ يجب أن تكون كلمة المرور {MIN_PASSWORD_LEN} أحرف على الأقل")
            else:
                try:
                    with get_db() as conn:
                        conn.execute(
                            "INSERT INTO users VALUES (?,?,?)",
                            (new_user, hash_password(new_pass), new_role)
                        )
                    st.success(f"✅ تم إضافة المستخدم '{new_user}' بنجاح")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("⚠️ اسم المستخدم موجود مسبقاً")

    st.divider()

    st.subheader("🗑️ حذف مستخدم")
    deletable = [u for u in users_df['username'].tolist() if u != st.session_state.username]
    if deletable:
        with st.form("delete_user_form"):
            del_user = st.selectbox("اختر المستخدم للحذف", deletable)
            if st.form_submit_button("حذف", use_container_width=True):
                with get_db() as conn:
                    conn.execute("DELETE FROM users WHERE username=?", (del_user,))
                st.success(f"✅ تم حذف المستخدم '{del_user}'")
                st.rerun()
    else:
        st.info("لا يوجد مستخدمون آخرون للحذف.")

    st.divider()

    st.subheader("🔑 تغيير كلمة مرور مستخدم")
    with st.form("change_pass_form"):
        chg_user = st.selectbox("اختر المستخدم", users_df['username'].tolist(), key="chg_user")
        chg_pass = st.text_input("كلمة المرور الجديدة", type="password")
        if st.form_submit_button("تحديث", use_container_width=True):
            if not chg_pass:
                st.warning("أدخل كلمة المرور الجديدة")
            elif len(chg_pass) < MIN_PASSWORD_LEN:
                st.warning(f"⚠️ يجب أن تكون كلمة المرور {MIN_PASSWORD_LEN} أحرف على الأقل")
            else:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE users SET password=? WHERE username=?",
                        (hash_password(chg_pass), chg_user)
                    )
                st.success(f"✅ تم تحديث كلمة مرور '{chg_user}'")

# ===================== زر تسجيل الخروج =====================

if st.sidebar.button("🚪 تسجيل الخروج / Logout"):
    st.session_state.clear()
    st.rerun()
