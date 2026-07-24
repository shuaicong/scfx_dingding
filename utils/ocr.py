"""OCR 文字识别工具

支持多后端自动检测：
- macOS: 使用系统 Vision 框架（VNRecognizeTextRequest），无需额外安装
- Linux: 使用 Tesseract OCR，需安装: apt-get install tesseract-ocr tesseract-ocr-chi-sim
"""
import logging
import platform
import re

logger = logging.getLogger(__name__)

_ocr_backend = None  # 'vision' or 'tesseract'


def _detect_backend() -> str | None:
    """自动检测可用的 OCR 后端"""
    global _ocr_backend
    if _ocr_backend:
        return _ocr_backend

    system = platform.system()

    # macOS: 优先使用 Vision 框架
    if system == "Darwin":
        try:
            from Cocoa import NSURL  # noqa: F401
            from Quartz import CGImageSourceCreateWithURL  # noqa: F401
            from Vision import VNRecognizeTextRequest  # noqa: F401
            _ocr_backend = "vision"
            logger.info("OCR 后端: macOS Vision 框架")
            return _ocr_backend
        except ImportError:
            logger.info("pyobjc 未安装，尝试 Tesseract 后端")

    # Linux / fallback: Tesseract
    try:
        import subprocess
        result = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # 检查中文语言包
            lang_result = subprocess.run(
                ["tesseract", "--list-langs"],
                capture_output=True, text=True, timeout=5,
            )
            has_chinese = "chi_sim" in lang_result.stdout or "chi_sim" in lang_result.stderr
            _ocr_backend = "tesseract"
            logger.info("OCR 后端: Tesseract%s", " (含中文)" if has_chinese else "")
            return _ocr_backend
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    _ocr_backend = None
    logger.warning("未检测到可用的 OCR 后端")
    return None


def is_available() -> bool:
    """检查是否有可用的 OCR 后端"""
    return _detect_backend() is not None


def get_backend() -> str | None:
    """返回当前使用的 OCR 后端名称: 'vision', 'tesseract', 或 None"""
    return _detect_backend()


# ====================================================================
# macOS Vision 后端
# ====================================================================

def _run_vision_request(image_path: str) -> tuple[list[str], list[tuple[float, float, str]]]:
    """使用 Vision 框架识别图片，返回 (文本列表, [(center_y, center_x, text)])"""
    from Cocoa import NSURL  # type: ignore
    from Quartz import (  # type: ignore
        CGImageSourceCreateWithURL,
        CGImageSourceCreateImageAtIndex,
        kCGImageSourceShouldCache,
    )
    from Vision import VNRecognizeTextRequest, VNImageRequestHandler  # type: ignore

    url = NSURL.fileURLWithPath_(image_path)
    options = {kCGImageSourceShouldCache: False}
    img_source = CGImageSourceCreateWithURL(url, options)
    if not img_source:
        raise RuntimeError(f"无法加载图片: {image_path}")

    cg_image = CGImageSourceCreateImageAtIndex(img_source, 0, None)
    if not cg_image:
        raise RuntimeError(f"无法解码图片: {image_path}")

    done = __import__("threading").Event()
    ocr_error = []
    cells = []  # (center_y, center_x, text)

    def completion_handler(request, error):
        if error:
            ocr_error.append(error.localizedDescription())
        elif request.results():
            for obs in request.results():
                bb = obs.boundingBox()
                cy = bb.origin.y + bb.size.height / 2
                cx = bb.origin.x + bb.size.width / 2
                candidates = obs.topCandidates_(1)
                if candidates and candidates[0]:
                    cells.append((cy, cx, candidates[0].string()))
        done.set()

    request = VNRecognizeTextRequest.alloc().initWithCompletionHandler_(completion_handler)
    request.setRecognitionLanguages_(["zh-Hans", "en-US"])
    request.setRecognitionLevel_(0)  # 0=fast
    request.setUsesLanguageCorrection_(True)

    handler = VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    handler.performRequests_error_([request], None)

    if not done.wait(timeout=30):
        raise TimeoutError("OCR 识别超时")
    if ocr_error:
        raise RuntimeError(f"OCR 识别失败: {ocr_error[0]}")

    # 按阅读顺序排序
    cells.sort(key=lambda c: (-c[0], c[1]))
    texts = [c[2] for c in cells]
    return texts, cells


def _ocr_vision(image_path: str) -> str:
    """使用 macOS Vision 框架识别图片文字"""
    texts, _ = _run_vision_request(image_path)
    return "\n".join(texts)


# ====================================================================
# Tesseract 后端
# ====================================================================

def _ocr_tesseract(image_path: str) -> str:
    """使用 Tesseract OCR 识别图片文字"""
    try:
        from PIL import Image
        import pytesseract

        img = Image.open(image_path)
        custom_config = r"--oem 3 --psm 6"
        text = pytesseract.image_to_string(
            img, lang="chi_sim+eng", config=custom_config,
        )
        return text.strip()
    except ImportError:
        raise RuntimeError("请安装 pytesseract 和 Pillow: pip install pytesseract pillow")
    except Exception as e:
        raise RuntimeError(f"Tesseract OCR 识别失败: {e}")


def _ocr_tesseract_table(image_path: str) -> str:
    """使用 Tesseract 识别表格图片，返回 Markdown 表格"""
    try:
        from PIL import Image
        import pytesseract

        img = Image.open(image_path)
        img_w, img_h = img.size

        # 获取详细位置信息
        data = pytesseract.image_to_data(
            img, lang="chi_sim+eng",
            config=r"--oem 3 --psm 6",
            output_type=pytesseract.Output.DICT,
        )

        # 筛选置信度 > 20 的文本块
        cells = []
        for i in range(len(data["text"])):
            text = (data["text"][i] or "").strip()
            conf = int(data["conf"][i]) if data["conf"][i] != "-1" else 0
            if text and conf > 20:
                x = data["left"][i] + data["width"][i] / 2
                y = data["top"][i] + data["height"][i] / 2
                # 归一化坐标
                cells.append((1 - y / img_h, x / img_w, text))

        if len(cells) < 5:
            # 识别结果太少，回退纯文本
            return _ocr_tesseract(image_path)

        rows = _group_into_rows(cells)
        # 过滤非表格行
        table_rows = [r for r in rows if len(r) >= 3]
        if table_rows:
            return _build_transaction_records(table_rows)
        return _ocr_tesseract(image_path)
    except ImportError:
        raise RuntimeError("请安装 pytesseract 和 Pillow: pip install pytesseract pillow")
    except Exception as e:
        raise RuntimeError(f"Tesseract 表格识别失败: {e}")


# ====================================================================
# 表格重建（利用坐标信息）
# ====================================================================

_ROW_GAP_THRESHOLD = 0.04  # 行间距阈值（归一化坐标）


def _group_into_rows(cells: list[tuple[float, float, str]]) -> list[list[tuple[float, str]]]:
    """将细胞按 Y 坐标分组为行

    Returns:
        [[(cy, cx, text), ...], ...]  每行内按 X 排序
    """
    if not cells:
        return []

    cells_sorted = sorted(cells, key=lambda c: -c[0])

    rows = []
    current_row = []

    for cell in cells_sorted:
        cy, cx, text = cell
        if not current_row:
            current_row.append(cell)
            continue

        prev_cy = current_row[-1][0]
        if prev_cy - cy > _ROW_GAP_THRESHOLD:
            current_row.sort(key=lambda x: x[1])
            rows.append(current_row)
            current_row = [cell]
        else:
            current_row.append(cell)

    if current_row:
        current_row.sort(key=lambda x: x[1])
        rows.append(current_row)

    return rows


def _is_header_row(row: list[tuple[float, float, str]]) -> bool:
    """判断是否为表头行"""
    header_kw = {"品种", "等级", "产地", "标的号", "年份", "交易模式",
                 "委托方", "起始价", "成交价", "成交"}
    text = "".join(t for _, _, t in row)
    matches = sum(1 for kw in header_kw if kw in text)
    return matches >= 2 and len(row) >= 4


def _build_transaction_records(rows: list[list[tuple[float, float, str]]]) -> str:
    """将 OCR 行列数据构建为可读的交易记录文本

    每条交易输出两行：
      品种(等级)/产地 | 年份 | 数量吨 | 起始价→成交价元/吨 | 交易模式
      委托方:xxx 标的号:xxx

    比 Markdown 表格更适合 12 列宽表场景。
    """
    if not rows:
        return ""

    # 找表头行
    header_idx = -1
    for i, row in enumerate(rows):
        if _is_header_row(row):
            header_idx = i
            break
    if header_idx < 0:
        return "\n".join(" | ".join(t for _, _, t in r) for r in rows)

    header_cells = sorted(rows[header_idx], key=lambda c: c[1])
    header_texts = [t for _, _, t in header_cells]
    header_xs = [x for _, x, _ in header_cells]
    col_count = len(header_texts)
    if col_count < 2:
        return ""

    # 列名到索引的映射
    col_names = {t: i for i, t in enumerate(header_texts)}

    def get_col(row_data: list[tuple[float, float, str]], cx: float) -> int:
        return min(range(col_count), key=lambda i: abs(cx - header_xs[i]))

    def collect_row_data(row_data):
        """将一行的碎片按列收集、按 Y 合并"""
        col_frags = [[] for _ in range(col_count)]
        for cy, cx, text in row_data:
            col_frags[get_col(row_data, cx)].append((cy, text))
        cells = []
        for frags in col_frags:
            if not frags:
                cells.append("")
            else:
                frags.sort(key=lambda x: -x[0])
                cells.append("".join(t for _, t in frags))
        return cells

    def name_match(*keys):
        """找到第一个匹配的列名索引"""
        for k in keys:
            for n in col_names:
                if k in n:
                    return col_names[n]
        return -1

    # 列索引（容错查找）
    idx_品种 = name_match("品种")
    idx_等级 = name_match("等级")
    idx_产地 = name_match("产地")
    idx_年份 = name_match("年份")
    idx_数量 = name_match("熬吧", "数量")
    idx_成交量 = name_match("成交里", "成交")
    idx_起始价 = name_match("起始价")
    idx_成交价 = name_match("成交价", "成交")
    idx_交易模式 = name_match("交易模式", "交易")
    idx_标的号 = name_match("标的号")
    idx_委托方 = name_match("委托方")
    idx_交易会 = name_match("交易会")
    if idx_成交量 == idx_起始价:  # fix: 成交量 vs 起始价
        idx_成交量 = name_match("成交里")

    records = []
    for row in rows:
        if row is rows[header_idx]:
            continue
        cells = collect_row_data(row_data=row)

        def cell(idx):
            return cells[idx] if 0 <= idx < col_count else ""

        # 构建产品线：品种(等级)/产地 | 年份
        product_parts = []
        v = cell(idx_品种)
        g = cell(idx_等级)
        if v and g:
            product_parts.append(f"{v}({g})")
        elif v:
            product_parts.append(v)

        o = cell(idx_产地)
        if o:
            product_parts.append(o)
        product_line = "/".join(product_parts) if product_parts else ""
        yr = cell(idx_年份)

        # 数量 + 价格线
        qty = cell(idx_数量)
        sp = cell(idx_起始价)
        ep = cell(idx_成交价)
        mode = cell(idx_交易模式)

        # 验证是否为价格数值（含数字即为价格）
        def is_price(v):
            return any(c.isdigit() for c in v) if v else False

        price_parts = []
        if qty:
            price_parts.append(f"{qty}吨")
        if is_price(sp) and is_price(ep) and sp != ep:
            price_parts.append(f"{sp}→{ep}元/吨")
        elif is_price(ep):
            price_parts.append(f"{ep}元/吨")
        elif is_price(sp):
            price_parts.append(f"起{sp}元/吨")
        if mode and "元/吨" not in mode:
            price_parts.append(mode)

        # 委托信息
        agent_parts = []
        ent = cell(idx_委托方)
        bid = cell(idx_标的号)
        if ent:
            agent_parts.append(f"委托方:{ent}")
        if bid:
            agent_parts.append(f"标的:{bid}")

        # 组装
        line1 = " | ".join(filter(None, [product_line, yr] if yr else [product_line]))
        if price_parts:
            line1 = (line1 + " | " if line1 else "") + " ".join(price_parts)
        line2 = " | ".join(agent_parts) if agent_parts else ""

        record = line1
        if record:
            if line2:
                record += "\n  " + line2
            records.append(record)

    # 过滤"小计"等非交易行
    records = [r for r in records if "小计" not in r and not r.startswith("0吨")]
    # 过滤小计等非交易行、以及纯数字行
    records = [
        r for r in records
        if "小计" not in r
        and not re.match(r"^[\d\.\,]+\s*吨$", r.split("\n")[0].strip())
    ]
    return "\n---\n".join(records) if records else ""


def recognize_table(image_path: str) -> str:
    """识别图片中的表格并转为 Markdown 表格

    利用 OCR 坐标信息重建行列结构，返回标准 Markdown 表格格式。

    Args:
        image_path: 图片文件路径

    Returns:
        Markdown 表格字符串
    """
    backend = _detect_backend()

    if backend == "vision":
        _, cells = _run_vision_request(image_path)
        rows = _group_into_rows(cells)
        # 过滤非表格行（标题/单位等行只有1-2个细胞）
        table_rows = [r for r in rows if len(r) >= 3]
        if table_rows:
            return _build_transaction_records(table_rows)
        # fallback: 返回纯文本
        return "\n".join(t for _, _, t in cells)

    elif backend == "tesseract":
        # Tesseract: 用 image_to_data 获取位置信息重建表格
        return _ocr_tesseract_table(image_path)

    raise RuntimeError("无可用的 OCR 后端")


# ====================================================================
# 公共接口
# ====================================================================

def recognize_text(image_path: str) -> str:
    """识别图片中的文字，自动选择可用的 OCR 后端

    Args:
        image_path: 图片文件路径

    Returns:
        识别出的文本，每行一个文本块
    """
    backend = _detect_backend()
    if not backend:
        raise RuntimeError(
            "无可用的 OCR 后端。"
            " macOS: pip install pyobjc-framework-Vision"
            " | Linux: apt-get install tesseract-ocr tesseract-ocr-chi-sim"
        )

    if backend == "vision":
        return _ocr_vision(image_path)
    elif backend == "tesseract":
        return _ocr_tesseract(image_path)
    else:
        raise RuntimeError(f"未知的 OCR 后端: {backend}")
