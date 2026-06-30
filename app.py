import streamlit as st
import sqlite3
import pandas as pd
import os
import re
import base64
import streamlit.components.v1 as components

# -----------------------------------------------------------------------------
# 全局配置与样式初始化
# -----------------------------------------------------------------------------
st.set_page_config(page_title="学术成果管理系统", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "academic_db.sqlite")
BG_IMG_PATH = os.path.join(BASE_DIR, "background.jpg")

def inject_custom_css():
    """
    注入全局自定义 CSS 样式
    包含背景图渲染及内容区底板配置
    """
    css_rules = "<style>\n"
    
    if os.path.exists(BG_IMG_PATH):
        with open(BG_IMG_PATH, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode()
        css_rules += f"""
        .stApp {{
            background: url("data:image/jpeg;base64,{b64_data}");
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
        }}
        /* 内容区底板，适配深浅色模式 */
        .block-container {{
            background-color: var(--background-color);
            opacity: 0.95;
            padding: 2.5rem;
            border-radius: 12px;
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.5);
        }}
        """
        
    css_rules += "</style>"
    st.markdown(css_rules, unsafe_allow_html=True)

inject_custom_css()

# -----------------------------------------------------------------------------
# 数据库核心操作
# -----------------------------------------------------------------------------
def get_connection():
    """建立并返回 SQLite 数据库连接"""
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def format_citation(row):
    """
    将数据库记录格式化为 GB/T 7714-2015 标准引用样式
    :param row: 包含单条成果记录的字典或 pandas Series
    :return: 格式化后的引用字符串
    """
    cat = row['category']
    authors = row['authors'] if row['authors'] else ""
    title = row['title']
    year = row['year'] if row['year'] else ""
    
    if cat == "期刊论文":
        source = row['source'] if row['source'] else ""
        details = row['details'] if row['details'] else ""
        cite_str = f"{authors}. {title}[J]. {source}, {year}"
        if details:
            cite_str += f", {details}."
        else:
            cite_str += "."
        return cite_str
        
    elif cat == "会议论文":
        source = row['source'] if row['source'] else ""
        return f"{authors}. {title}[C]// {source}. {year}."
        
    elif cat == "发明专利":
        identifier = row['identifier'] if row['identifier'] else ""
        prefix = "申请号：" if row['status'] == "公开" else "专利号："
        if "申请号" in identifier or "专利号" in identifier:
            prefix = "" 
        return f"{authors}. {title}[P]. {prefix}{identifier}."
        
    elif cat == "软件著作权":
        identifier = row['identifier'] if row['identifier'] else ""
        return f"{title}[CP]. 登记号：{identifier}."
        
    return f"{title}"

def get_duplicate_achievement(title):
    """
    检查数据库中是否已存在同名成果
    :return: 如果存在则返回第一条匹配的记录(Series)，否则返回 None
    """
    if not title:
        return None
    conn = get_connection()
    # 使用 LIKE 实现忽略大小写的精确匹配检查
    df = pd.read_sql_query("SELECT * FROM achievements WHERE title LIKE ?", conn, params=(title.strip(),))
    conn.close()
    if not df.empty:
        return df.iloc[0]
    return None

# -----------------------------------------------------------------------------
# 侧边栏导航与状态管理
# -----------------------------------------------------------------------------
st.sidebar.title("系统导航")
menu = st.sidebar.radio(
    "功能模块", 
    ["成果检索与修改", "成果信息录入", "作者别名管理", "成果信息删除"],
    label_visibility="collapsed"
)

# 确保切换菜单时清理相关的页面层级状态
if 'current_menu' not in st.session_state:
    st.session_state['current_menu'] = menu
    st.session_state['editing_id'] = None
    st.session_state['delete_id'] = None
    st.session_state['editing_std_name'] = None
elif st.session_state['current_menu'] != menu:
    st.session_state['current_menu'] = menu
    st.session_state['editing_id'] = None
    st.session_state['delete_id'] = None
    st.session_state['editing_std_name'] = None
    st.session_state['show_export_page'] = False

# -----------------------------------------------------------------------------
# 高级搜索辅助函数
# -----------------------------------------------------------------------------
def row_match(row, kw, alias_map):
    # 1. 检查所有文本字段
    text_to_search = f"{row.get('year','')} {row.get('title','')} {row.get('source','')} {row.get('status','')} {row.get('identifier','')}"
    if kw.lower() in text_to_search.lower():
        return True
    
    # 2. 检查作者字段及其别名映射
    row_authors = str(row.get('authors', '')).lower()
    if kw.lower() in row_authors:
        return True
    
    for std_name, aliases in alias_map.items():
        # 如果搜索关键字是某个标准名或其别名
        if kw.lower() in std_name.lower() or any(kw.lower() in a.lower() for a in aliases):
            # 则反向检查该条记录的作者里是否包含这个标准名或其兄弟别名
            if std_name.lower() in row_authors or any(a.lower() in row_authors for a in aliases):
                return True
    return False

def evaluate_query(row, query, alias_map):
    if not query.strip():
        return True
    
    # 处理 OR 逻辑: 使用 | 符号分割
    or_blocks = query.split('|')
    for block in or_blocks:
        # 处理 AND 逻辑: 使用空格分割
        tokens = block.strip().split()
        block_match = True
        for token in tokens:
            token = token.strip()
            if not token: continue
            
            is_not = False
            # 识别排除符（如 -2025）
            if token.startswith('-'):
                is_not = True
                token = token[1:].strip()
            
            # 排除独立的无意义负号
            if not token: continue
            
            match_token = row_match(row, token, alias_map)
            if is_not:
                match_token = not match_token
                
            if not match_token:
                block_match = False
                break
                
        # 任何一个 OR 块成立，整条语句就成立
        if block_match:
            return True
    return False

# -----------------------------------------------------------------------------
# 模块：成果检索与修改
# -----------------------------------------------------------------------------
if menu == "成果检索与修改":
    
    if 'editing_id' not in st.session_state:
        st.session_state['editing_id'] = None
    
    if 'success_msg' in st.session_state:
        st.success(st.session_state['success_msg'])
        del st.session_state['success_msg']
        
    # 记录持久化搜索状态，实现跨页面记忆和消除回车延迟
    if 'saved_search_cat' not in st.session_state:
        st.session_state['saved_search_cat'] = "全部"
    if 'saved_search_kw' not in st.session_state:
        st.session_state['saved_search_kw'] = ""
        
    def update_search_state():
        """回调函数：实时同步组件状态到持久化缓存中，解决迟滞Bug"""
        st.session_state['saved_search_cat'] = st.session_state['search_cat_widget']
        st.session_state['saved_search_kw'] = st.session_state['search_kw_widget']

    # ======================================
    # 视图：批量导出/打印页面
    # ======================================
    if st.session_state.get('show_export_page'):
        st.header("📄 批量打印与导出")
        st.info("以下为您检索到的所有记录的 GB/T 7714-2015 格式文本，可直接 `Ctrl+A` 全选复制。")
        if st.button("⬅️ 返回检索列表", type="primary"):
            st.session_state['show_export_page'] = False
            st.rerun()
        
        export_df = st.session_state.get('export_df', pd.DataFrame())
        export_text = ""
        for i, (_, row) in enumerate(export_df.iterrows()):
            export_text += f"[{i+1}] {format_citation(row)}\n"
        
        st.code(export_text, language="text")
        st.stop()  # 停止渲染下方原有的检索界面
        
    # ======================================
    # 视图一：检索列表
    # ======================================
    if st.session_state.get('editing_id') is None:
        st.header("成果检索与修改")
        
        # 绑定状态到 key，并使用 on_change 回调
        category_filter = st.radio(
            "选择成果类别", 
            ["全部", "期刊论文", "会议论文", "发明专利", "软件著作权"], 
            horizontal=True,
            index=["全部", "期刊论文", "会议论文", "发明专利", "软件著作权"].index(st.session_state['saved_search_cat']),
            key="search_cat_widget",
            on_change=update_search_state
        )
        
        st.markdown("<small>💡 **高级搜索提示**: **空格** 表示与(AND) 例如 `2025 龚文杰`； **|** 表示或(OR) 例如 `2025|2026`； **-** 表示排除(NOT) 例如 `张广辉 -2025`。</small>", unsafe_allow_html=True)
        search_query = st.text_input(
            "输入检索关键字 (输入后按下回车键立即筛选)", 
            value=st.session_state['saved_search_kw'],
            key="search_kw_widget",
            on_change=update_search_state
        )
        
        conn = get_connection()
        # 默认按年份和ID倒序，保证最新的在最上面
        df = pd.read_sql_query("SELECT * FROM achievements ORDER BY year DESC, id DESC", conn)
        
        # 提取别名字典映射供高级搜索使用
        cursor = conn.cursor()
        cursor.execute("SELECT standard_name, alias FROM author_aliases")
        alias_map = {}
        for std, alias in cursor.fetchall():
            alias_map.setdefault(std, set()).add(alias)
        conn.close()
        
        # 应用过滤逻辑 (使用及时更新好的 widget 变量)
        if category_filter != "全部":
            df = df[df['category'] == category_filter]
        if search_query:
            df = df[df.apply(lambda row: evaluate_query(row, search_query, alias_map), axis=1)]

        if df.empty:
            st.write("未检索到匹配的成果数据。")
        else:
            # 增加批量导出按钮布局
            col_res, col_exp = st.columns([7, 3])
            col_res.write(f"共检索到 {len(df)} 条记录：")
            if col_exp.button("📄 批量导出当前结果", use_container_width=True):
                st.session_state['export_df'] = df
                st.session_state['show_export_page'] = True
                st.rerun()
            
            for index, row in df.iterrows():
                # 埋入 HTML 锚点
                st.markdown(f"<div id='item_{row['id']}'></div>", unsafe_allow_html=True)
                col1, col2 = st.columns([9, 1])
                
                # 安全提取并格式化年份前缀
                year_val = row['year']
                year_prefix = f"[{int(year_val)}]" if pd.notna(year_val) and year_val else "[无年份]"
                display_text = f"**{year_prefix}** [{row['status']}] {format_citation(row)}"
                
                col1.markdown(display_text)
                if col2.button("详情", key=f"btn_{row['id']}"):
                    st.session_state['editing_id'] = row['id']
                    st.rerun()

            # 执行滚动位置还原 JS 脚本
            if st.session_state.get('last_viewed_id'):
                components.html(
                    f"""
                    <script>
                    setTimeout(function() {{
                        var target = window.parent.document.getElementById('item_{st.session_state['last_viewed_id']}');
                        if (target) {{
                            target.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                        }}
                    }}, 150);
                    </script>
                    """,
                    height=0
                )
                st.session_state['last_viewed_id'] = None

    # 视图二：详情与属性修改
    else:
        st.header("成果详情与修改")
            
        conn = get_connection()
        target_row = pd.read_sql_query(
            "SELECT * FROM achievements WHERE id=?", 
            conn, params=(st.session_state['editing_id'],)
        ).iloc[0]
        conn.close()

        st.subheader("标准引用信息 (GB/T 7714-2015)")
        st.code(format_citation(target_row), language="text")

        st.subheader("修改属性信息")
        with st.form("edit_form"):
            cat = target_row['category']
            
            status_options = ["未投稿", "待录用", "网上发表", "出版"] if cat == "期刊论文" else \
                             ["未发表", "已发表"] if cat == "会议论文" else \
                             ["公开", "授权"] if cat == "发明专利" else ["已登记"]
            
            current_status = target_row['status']
            if current_status not in status_options and current_status:
                status_options.append(current_status)
            status_index = status_options.index(current_status) if current_status in status_options else 0
            
            new_status = st.selectbox("状态", options=status_options, index=status_index)
            
            # 初始化占位变量防止后续逻辑报错
            new_title = target_row['title']
            new_authors = target_row['authors']
            new_year = target_row['year']
            new_source = target_row['source']
            new_details = target_row['details']
            new_identifier = target_row['identifier']
            
            # 基于不同类别的定制化表单
            if cat == "期刊论文":
                new_title = st.text_input("文章名", value=target_row['title'] if target_row['title'] else "")
                new_authors = st.text_input("作者", value=target_row['authors'] if target_row['authors'] else "")
                new_source = st.text_input("期刊名称", value=target_row['source'] if target_row['source'] else "")
                new_year = st.number_input("年份", value=int(target_row['year']) if pd.notna(target_row['year']) else 2025, step=1)
                new_details = st.text_input("卷期页码", value=target_row['details'] if target_row['details'] else "")
                
            elif cat == "会议论文":
                new_title = st.text_input("文章名", value=target_row['title'] if target_row['title'] else "")
                new_authors = st.text_input("作者", value=target_row['authors'] if target_row['authors'] else "")
                new_source = st.text_input("会议名称", value=target_row['source'] if target_row['source'] else "")
                new_year = st.number_input("年份", value=int(target_row['year']) if pd.notna(target_row['year']) else 2025, step=1)
                
            elif cat == "发明专利":
                new_title = st.text_input("专利名称", value=target_row['title'] if target_row['title'] else "")
                new_authors = st.text_input("发明人", value=target_row['authors'] if target_row['authors'] else "")
                id_label = "申请号" if new_status == "公开" else "公开号/专利号"
                new_identifier = st.text_input(id_label, value=target_row['identifier'] if target_row['identifier'] else "")
                new_year = st.number_input("年份", value=int(target_row['year']) if pd.notna(target_row['year']) else 2025, step=1)
                
            elif cat == "软件著作权":
                new_title = st.text_input("软件名称", value=target_row['title'] if target_row['title'] else "")
                new_identifier = st.text_input("登记号", value=target_row['identifier'] if target_row['identifier'] else "")
            
            submit_update = st.form_submit_button("保存修改")
            
            if submit_update:
                # 核心信息非空校验
                valid = True
                if not new_title: valid = False
                if cat in ["期刊论文", "会议论文", "发明专利"] and not new_authors: valid = False
                if cat in ["期刊论文", "会议论文"] and not new_source: valid = False
                if cat in ["发明专利", "软件著作权"] and not new_identifier: valid = False
                
                if not valid:
                    st.error("保存失败：原有的关键核心信息不能修改为空。")
                else:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE achievements 
                        SET status=?, title=?, authors=?, year=?, source=?, details=?, identifier=?
                        WHERE id=?
                    """, (new_status, new_title, new_authors, new_year, new_source, new_details, new_identifier, int(st.session_state['editing_id'])))
                    conn.commit()
                    conn.close()
                    st.session_state['success_msg'] = "成果信息修改已成功保存。"
                    st.session_state['last_viewed_id'] = st.session_state['editing_id']
                    st.session_state['editing_id'] = None
                    st.rerun()

        st.write("---")
        if st.button("⬅️ 返回列表 (放弃未保存的修改)", use_container_width=True, type="primary"):
            st.session_state['last_viewed_id'] = st.session_state['editing_id']
            st.session_state['editing_id'] = None
            st.rerun()

# -----------------------------------------------------------------------------
# 模块：成果信息录入
# -----------------------------------------------------------------------------
elif menu == "成果信息录入":
    st.header("成果信息录入")
    
    if 'success_msg' in st.session_state:
        st.success(st.session_state['success_msg'])
        del st.session_state['success_msg']
    
    category = st.selectbox("选择成果类别", ["期刊论文", "会议论文", "发明专利", "软件著作权"])
    
    if 'parsed_data' not in st.session_state:
        st.session_state['parsed_data'] = {"authors": "", "title": "", "source": "", "year": 2025, "details": ""}
    
    if category == "期刊论文":
        status = st.selectbox("选择状态", ["未投稿", "待录用", "网上发表", "出版"])
        
        st.subheader("快速解析（可选）")
        raw_citation = st.text_area("粘贴引文格式（如 GB/T 7714-2015），系统将尝试自动提取字段：", height=100)
        
        if st.button("解析引文"):
            raw = raw_citation.replace("\n", "").strip()
            pattern = r"^(?:\[\d+\]\s*)?(.*?)\.\s*(.*?)\[J\]\.\s*(.*?),\s*(\d{4})(?:,\s*(.*?))?\.?$"
            match = re.search(pattern, raw)
            
            if match:
                st.session_state['parsed_data']['authors'] = match.group(1).strip()
                st.session_state['parsed_data']['title'] = match.group(2).strip()
                st.session_state['parsed_data']['source'] = match.group(3).strip()
                st.session_state['parsed_data']['year'] = int(match.group(4))
                if match.group(5):
                    st.session_state['parsed_data']['details'] = match.group(5).strip()
                st.success("解析成功：已提取 GB/T 7714-2015 格式数据。")
            else:
                st.warning("严谨标准解析失败，系统已尝试模糊提取，请仔细核对下方表单数据。")
                authors_match = re.search(r'^(?:\[\d+\]\s*)?(.*?)\.\s+', raw)
                year_match = re.search(r',\s*([12][0-9]{3})\s*,?', raw)
                
                if authors_match: st.session_state['parsed_data']['authors'] = authors_match.group(1).strip()
                if year_match: st.session_state['parsed_data']['year'] = int(year_match.group(1))
                
                temp_title = re.sub(r'^(?:\[\d+\]\s*)?(.*?)\.\s+', '', raw)
                title_match = temp_title.split('[J]')
                if len(title_match) > 1:
                    st.session_state['parsed_data']['title'] = title_match[0].strip()
                    source_part = title_match[1].replace('.', '').split(',')
                    if len(source_part) > 0:
                        st.session_state['parsed_data']['source'] = source_part[0].strip()
            
            st.rerun()

        st.subheader("详细信息确认")
        with st.form("journal_form"):
            col1, col2 = st.columns(2)
            with col1:
                authors = st.text_input("作者", value=st.session_state['parsed_data'].get('authors', ''))
                title = st.text_input("文章名", value=st.session_state['parsed_data'].get('title', ''))
                source = st.text_input("期刊名称", value=st.session_state['parsed_data'].get('source', ''))
            with col2:
                year = st.number_input("年份", value=st.session_state['parsed_data'].get('year', 2025), step=1)
                details = st.text_input("卷期页码")
            
            submit = st.form_submit_button("入库保存")
            if submit:
                if not authors or not title:
                    st.error("作者和文章名不能为空。")
                elif status in ["网上发表", "出版"] and (not source or not details):
                    st.error("当前状态下，必须补全期刊名称和卷期页码信息。")
                else:
                    dup = get_duplicate_achievement(title)
                    if dup is not None:
                        st.error(f"🚫 **禁止录入：系统检测到同名成果已存在！**\n\n已存在成果信息：{format_citation(dup)}")
                    else:
                        conn = get_connection()
                        conn.execute("INSERT INTO achievements (category, status, title, authors, year, source, details) VALUES (?,?,?,?,?,?,?)",
                                     (category, status, title, authors, year, source, details))
                        conn.commit(); conn.close()
                        st.session_state['parsed_data'] = {}
                        st.session_state['success_msg'] = "期刊论文录入成功。"
                        st.rerun()
                    
    elif category == "会议论文":
        status = st.selectbox("选择状态", ["未发表", "已发表"])
        
        st.subheader("快速解析（可选）")
        raw_citation = st.text_area("粘贴引文格式（如 GB/T 7714-2015），系统将尝试自动提取字段：", height=100)
        
        if st.button("解析引文"):
            raw = raw_citation.replace("\n", "").strip()
            pattern = r"^(?:\[\d+\]\s*)?(.*?)\.\s*(.*?)\[C\]\/\/\s*([^.]+)\.(?:.*?)(\d{4})\.?$"
            match = re.search(pattern, raw)
            
            if match:
                st.session_state['parsed_data']['authors'] = match.group(1).strip()
                st.session_state['parsed_data']['title'] = match.group(2).strip()
                st.session_state['parsed_data']['source'] = match.group(3).strip()
                st.session_state['parsed_data']['year'] = int(match.group(4))
                st.success("解析成功：已提取会议论文数据。")
            else:
                st.warning("严谨标准解析失败，系统已尝试模糊提取，请仔细核对下方表单数据。")
                authors_match = re.search(r'^(?:\[\d+\]\s*)?(.*?)\.\s+', raw)
                year_match = re.search(r'\b(19\d{2}|20\d{2})\b', raw)
                
                if authors_match: st.session_state['parsed_data']['authors'] = authors_match.group(1).strip()
                if year_match: st.session_state['parsed_data']['year'] = int(year_match.group(1))
                
                temp_title = re.sub(r'^(?:\[\d+\]\s*)?(.*?)\.\s+', '', raw)
                title_match = temp_title.split('[C]//')
                if len(title_match) > 1:
                    st.session_state['parsed_data']['title'] = title_match[0].strip()
                    source_part = title_match[1].split('.')
                    if len(source_part) > 0:
                        st.session_state['parsed_data']['source'] = source_part[0].strip()
            
            st.rerun()
            
        st.subheader("详细信息确认")
        with st.form("conference_form"):
            title = st.text_input("文章名", value=st.session_state['parsed_data'].get('title', ''))
            authors = st.text_input("作者", value=st.session_state['parsed_data'].get('authors', ''))
            source = st.text_input("会议名称", value=st.session_state['parsed_data'].get('source', ''))
            year = st.number_input("时间年份", value=st.session_state['parsed_data'].get('year', 2025), step=1)
            submit = st.form_submit_button("入库保存")
            if submit:
                if not authors or not title or not source:
                    st.error("作者、文章名和会议名称不能为空。")
                else:
                    dup = get_duplicate_achievement(title)
                    if dup is not None:
                        st.error(f"🚫 **禁止录入：系统检测到同名成果已存在！**\n\n已存在成果信息：{format_citation(dup)}")
                    else:
                        conn = get_connection()
                        conn.execute("INSERT INTO achievements (category, status, title, authors, year, source) VALUES (?,?,?,?,?,?)",
                                     (category, status, title, authors, year, source))
                        conn.commit(); conn.close()
                        st.session_state['parsed_data'] = {}
                        st.session_state['success_msg'] = "会议论文录入成功。"
                        st.rerun()

    elif category == "发明专利":
        status = st.selectbox("选择状态", ["公开", "授权"])
        with st.form("patent_form"):
            title = st.text_input("专利名称")
            authors = st.text_input("发明人")
            identifier_label = "申请号" if status == "公开" else "公开号/专利号"
            identifier = st.text_input(identifier_label)
            year = st.number_input("年份", value=2025, step=1)
            submit = st.form_submit_button("入库保存")
            if submit:
                if not authors or not title or not identifier:
                    st.error("发明人、专利名称和编号不能为空。")
                else:
                    dup = get_duplicate_achievement(title)
                    if dup is not None:
                        st.error(f"🚫 **禁止录入：系统检测到同名成果已存在！**\n\n已存在成果信息：{format_citation(dup)}")
                    else:
                        conn = get_connection()
                        conn.execute("INSERT INTO achievements (category, status, title, authors, year, identifier) VALUES (?,?,?,?,?,?)",
                                     (category, status, title, authors, year, identifier))
                        conn.commit(); conn.close()
                        st.session_state['success_msg'] = "发明专利录入成功。"
                        st.rerun()

    elif category == "软件著作权":
        with st.form("software_form"):
            title = st.text_input("软件名称")
            identifier = st.text_input("登记号")
            submit = st.form_submit_button("入库保存")
            if submit:
                if not title or not identifier:
                    st.error("软件名称和登记号不能为空。")
                else:
                    dup = get_duplicate_achievement(title)
                    if dup is not None:
                        st.error(f"🚫 **禁止录入：系统检测到同名成果已存在！**\n\n已存在成果信息：{format_citation(dup)}")
                    else:
                        conn = get_connection()
                        conn.execute("INSERT INTO achievements (category, status, title, identifier) VALUES (?,?,?,?)",
                                     (category, "已登记", title, identifier))
                        conn.commit(); conn.close()
                        st.session_state['success_msg'] = "软件著作权录入成功。"
                        st.rerun()

# -----------------------------------------------------------------------------
# 模块：作者别名管理
# -----------------------------------------------------------------------------
elif menu == "作者别名管理":
    
    if 'success_msg' in st.session_state:
        st.success(st.session_state['success_msg'])
        del st.session_state['success_msg']

    conn = get_connection()

    # 视图一：标准名及别名分组列表
    if st.session_state.get('editing_std_name') is None:
        st.header("作者别名字典管理")
        st.info("系统支持一对多映射。建立映射后，在检索中输入相应的标准名或任意别名，即可统一汇集展示关联的全部成果记录。")

        st.subheader("新增作者别名分组")
        with st.form("add_group_form"):
            col1, col2 = st.columns(2)
            with col1:
                new_std = st.text_input("录入标准姓名 (例: 龚文杰)")
            with col2:
                new_alias = st.text_input("录入首个别名/缩写 (例: Gong W J)")
            
            if st.form_submit_button("创建映射分组"):
                if not new_std or not new_alias:
                    st.error("标准姓名与别名均不能为空。")
                else:
                    try:
                        conn.execute("INSERT INTO author_aliases (standard_name, alias) VALUES (?, ?)", (new_std, new_alias))
                        conn.commit()
                        st.session_state['success_msg'] = "新映射分组创建成功。"
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("操作失败：该别名在数据库中已存在，不支持重复绑定。")

        st.write("---")
        st.subheader("现有字典映射一览 (点击条目进入编辑)")
        
        # 将相同标准名的别名聚合展示
        df_aliases = pd.read_sql_query("SELECT standard_name, GROUP_CONCAT(alias, ', ') as aliases FROM author_aliases GROUP BY standard_name", conn)
        
        if df_aliases.empty:
            st.write("当前系统字典为空。")
        else:
            for index, row in df_aliases.iterrows():
                # 埋入字典界面的 HTML 锚点
                st.markdown(f"<div id='dict_{row['standard_name']}'></div>", unsafe_allow_html=True)
                col1, col2 = st.columns([9, 1])
                display_text = f"标准名: {row['standard_name']} | 关联别名: {row['aliases']}"
                col1.markdown(display_text)
                if col2.button("编辑", key=f"std_{row['standard_name']}"):
                    st.session_state['editing_std_name'] = row['standard_name']
                    st.rerun()

            # 执行滚动位置还原 JS 脚本
            if st.session_state.get('last_viewed_dict'):
                components.html(
                    f"""
                    <script>
                    setTimeout(function() {{
                        var target = window.parent.document.getElementById('dict_{st.session_state['last_viewed_dict']}');
                        if (target) {{
                            target.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                        }}
                    }}, 150);
                    </script>
                    """,
                    height=0
                )
                st.session_state['last_viewed_dict'] = None

    # 视图二：选定标准名的映射详情与编辑
    else:
        std_name = st.session_state['editing_std_name']
        st.header(f"管理映射字典: {std_name}")

        st.write("---")
        st.subheader("基础信息维护")
        with st.form("edit_std_name_form"):
            updated_std_name = st.text_input("修改当前标准姓名 (将同步更新该组下所有别名的绑定关系)", value=std_name)
            if st.form_submit_button("保存名称修改"):
                if not updated_std_name:
                    st.error("标准姓名不能为空。")
                elif updated_std_name != std_name:
                    conn.execute("UPDATE author_aliases SET standard_name = ? WHERE standard_name = ?", (updated_std_name, std_name))
                    conn.commit()
                    st.session_state['success_msg'] = "标准姓名修改成功。"
                    st.session_state['editing_std_name'] = updated_std_name
                    st.rerun()

        st.subheader("关联别名维护")
        df_items = pd.read_sql_query("SELECT id, alias FROM author_aliases WHERE standard_name=?", conn, params=(std_name,))
        
        # 逐条罗列包含的别名，并提供移除按钮
        for index, row in df_items.iterrows():
            col1, col2 = st.columns([9, 1])
            col1.write(f"- {row['alias']}")
            if col2.button("移除", key=f"del_alias_{row['id']}"):
                conn.execute("DELETE FROM author_aliases WHERE id=?", (int(row['id']),))
                conn.commit()
                st.rerun()

        # 在当前分组中追加新别名
        with st.form("add_alias_to_group_form"):
            additional_alias = st.text_input("为此标准姓名追加新的别名/缩写")
            if st.form_submit_button("追加别名"):
                if not additional_alias:
                    st.error("追加的别名不能为空。")
                else:
                    try:
                        conn.execute("INSERT INTO author_aliases (standard_name, alias) VALUES (?, ?)", (std_name, additional_alias))
                        conn.commit()
                        st.session_state['success_msg'] = "新别名追加成功。"
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("操作失败：该别名在数据库中已存在。")
                        
        st.write("---")
        if st.button("⬅️ 返回字典列表", use_container_width=True, type="primary"):
            st.session_state['last_viewed_dict'] = std_name
            st.session_state['editing_std_name'] = None
            st.rerun()
                        
    conn.close()

# -----------------------------------------------------------------------------
# 模块：成果信息删除
# -----------------------------------------------------------------------------
elif menu == "成果信息删除":
    
    if 'delete_id' not in st.session_state:
        st.session_state['delete_id'] = None
        
    if st.session_state['delete_id'] is None:
        st.header("成果信息删除")
        st.write("操作提示：数据删除后无法恢复，请谨慎操作。")
        
        conn = get_connection()
        df = pd.read_sql_query("SELECT * FROM achievements ORDER BY id DESC", conn)
        conn.close()
        
        if df.empty:
            st.write("数据库目前为空。")
        else:
            st.write("---")
            for index, row in df.iterrows():
                col1, col2 = st.columns([9, 1])
                
                # 安全提取并格式化年份前缀
                year_val = row['year']
                year_prefix = f"[{int(year_val)}]" if pd.notna(year_val) and year_val else "[无年份]"
                display_text = f"{year_prefix} {format_citation(row)}"
                
                col1.markdown(display_text)
                if col2.button("删除", key=f"del_{row['id']}"):
                    st.session_state['delete_id'] = row['id']
                    st.rerun()
    else:
        st.header("删除确认")
        
        conn = get_connection()
        target_title = pd.read_sql_query(
            "SELECT title FROM achievements WHERE id=?", 
            conn, params=(st.session_state['delete_id'],)
        ).iloc[0]['title']
        conn.close()
        
        st.error(f"待删除成果：{target_title}")
        st.write("是否确认执行彻底删除？")
        
        col1, col2 = st.columns(2)
        if col1.button("确认删除"):
            conn = get_connection()
            conn.execute("DELETE FROM achievements WHERE id = ?", (int(st.session_state['delete_id']),))
            conn.commit()
            conn.close()
            st.session_state['delete_id'] = None
            st.rerun()
            
        if col2.button("取消返回"):
            st.session_state['delete_id'] = None
            st.rerun()