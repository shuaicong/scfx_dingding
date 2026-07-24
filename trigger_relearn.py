"""自动触发 AI 助理知识库更新

通过 Playwright 无头浏览器打开 AI 助理对话页面，发送"调用技能更新"消息。
该页面通过 URL code 参数鉴权，无需额外登录。
"""
import json
import logging
import os

logger = logging.getLogger("trigger_relearn")

PROFILE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".playwright_profile"
)


class RelearnTrigger:
    """AI 助理知识库更新触发器"""

    COPILOT_URL = "https://agent.dingtalk.com/copilot?code=RSuW52hk5z&channel=%E8%87%AA%E5%8A%A8%E6%9B%B4%E6%96%B0%E7%9F%A5%E8%AF%86%E5%BA%93%E5%86%85%E5%AE%B9"

    # 知识库更新成功的响应特征
    RELEARN_SUCCESS_KEYWORDS = [
        "知识库记忆",
        "知识库查询无相关知识",
        "以下内容由模型为你继续回答",
        "知识库更新",
        "重新学习",
    ]

    def __init__(self, headless: bool = True):
        self.headless = headless
        os.makedirs(PROFILE_DIR, exist_ok=True)

    def trigger(self) -> dict:
        """执行触发操作

        Returns:
            {"success": bool, "response": str, "method": str}
        """
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                PROFILE_DIR,
                headless=self.headless,
                args=["--no-sandbox"],
            )

            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                return self._do_trigger(page)
            except Exception as e:
                logger.error("触发失败: %s", e)
                return {"success": False, "response": str(e), "method": "error"}
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    def _do_trigger(self, page) -> dict:
        """在浏览器页面中执行触发"""
        page.goto(self.COPILOT_URL, wait_until="networkidle")
        page.wait_for_timeout(5000)

        # 先尝试 UI 方式
        result = self._send_via_ui(page)
        if result["success"]:
            return result

        # 备选：RPC 直调
        logger.warning("UI 方式发送失败，尝试 RPC 直调...")
        return self._send_via_rpc(page)

    def _send_via_ui(self, page) -> dict:
        """通过 Cangjie 编辑器 UI 发送消息并提取回复"""
        try:
            from playwright.sync_api import TimeoutError as PwTimeout

            # ---- 1. 聚焦 Cangjie 编辑器（真正的输入区） ----
            page.evaluate("""
                (() => {
                    const el = document.querySelector('[data-cangjie-editable="true"]');
                    if (el) el.focus();
                })();
            """)
            page.wait_for_timeout(500)
            logger.info("已聚焦 Cangjie 编辑器")

            # ---- 2. 用 keyboard.type 输入文本 ----
            page.keyboard.type("调用技能更新", delay=80)
            page.wait_for_timeout(500)
            logger.info("已输入文本")

            # ---- 3. 记录发送前的页面文本快照 ----
            text_before = page.evaluate("() => document.body.innerText")

            # ---- 4. 点击发送按钮 ----
            send_btn = page.query_selector(".icon-wrapper.send-icon")
            if send_btn:
                send_btn.click()
                logger.info("已点击发送按钮")
            else:
                logger.warning("未找到发送按钮，尝试 Enter 键")
                page.keyboard.press("Enter")

            logger.info("已发送 '调用技能更新'，等待 AI 回复...")

            # ---- 5. 等待页面出现新的 Loading（AI 开始处理） ----
            try:
                page.wait_for_function(
                    "() => document.body.innerText.includes('Loading')",
                    timeout=15000,
                )
                logger.info("AI 开始处理...")
            except PwTimeout:
                logger.warning("未检测到加载状态")

            # ---- 6. 等待页面内容增长（AI 回复完成） ----
            # 用 text_before 对比，当页面文本显著增长时说明新回复已渲染
            text_before_len = len(text_before)
            try:
                page.wait_for_function(
                    f"""
                    () => {{
                        const current = document.body.innerText;
                        // 当前文本比发送前长超过 50 字符说明有新内容
                        return current.length > {text_before_len} + 50;
                    }}
                    """,
                    timeout=150000,
                )
                logger.info("检测到页面内容更新")
            except PwTimeout:
                logger.warning("等待页面内容更新超时")

            # 额外等待确保卡片渲染完成
            page.wait_for_timeout(8000)

            # ---- 7. 提取 AI 回复 ----
            response = self._extract_response(page, text_before)

            if response and response.strip():
                response = response.strip()
                logger.info("AI 回复: %s", response[:500])
            else:
                logger.warning("AI 回复为空")
                return {"success": True, "response": "(空回复)", "method": "ui"}

            # 检查知识库更新特征词
            matched = [kw for kw in self.RELEARN_SUCCESS_KEYWORDS if kw in response]
            if matched:
                logger.info("知识库更新成功! 检测到特征词: %s", matched)
            else:
                logger.info("AI 已回复（知识库更新特征词未匹配）")

            return {"success": True, "response": response, "method": "ui"}

        except Exception as e:
            logger.warning("UI 发送失败: %s", e, exc_info=True)
            return {"success": False, "response": str(e), "method": "ui"}

    def _extract_response(self, page, text_before: str = "") -> str:
        """从页面中提取 AI 回复文本（处理 DingTalk 卡片格式）

        Args:
            page: Playwright page
            text_before: 发送消息前的页面文本快照，用于提取新增内容
        """
        return page.evaluate("""
            (textBefore) => {
                var allText = document.body.innerText || '';

                // 策略1（最优）：如果提供了发送前的快照，取新增部分的最下方内容
                if (textBefore && allText.length > textBefore.length + 20) {
                    var newText = allText.substring(textBefore.length);
                    // 去掉开头的无关字符（如换行、空格）
                    newText = newText.replace(/^[\\s\\n]+/, '');
                    if (newText.length > 20) return newText;
                }

                // 策略2: 找 durbo 卡片中的 Markdown/richtext 内容（钉钉卡片格式）
                var cardSelectors = [
                    '[class*=durbo] [class*=Markdown]',
                    '[class*=durbo] [class*=richtext]',
                    '[class*=durbo] [class*=content]',
                ];
                for (var sel of cardSelectors) {
                    var els = document.querySelectorAll(sel);
                    if (els.length > 0) {
                        var last = els[els.length - 1];
                        var text = (last.innerText || last.textContent || '').trim();
                        if (text.length > 20) return text;
                    }
                }

                // 策略3: 找最后一个对话卡片的整体文本
                var convCards = document.querySelectorAll('[class*=ConversationCard], [class*=conversation-card], [class*=message-card]');
                if (convCards.length > 0) {
                    var lastCard = convCards[convCards.length - 1];
                    var text = (lastCard.innerText || lastCard.textContent || '').trim();
                    if (text.length > 20) return text;
                }

                // 策略4: 找"知识库记忆"关键词上下文
                var idx = allText.indexOf('知识库记忆');
                if (idx !== -1) {
                    var start = Math.max(0, idx - 200);
                    return allText.substring(start);
                }

                // 策略5: 最后 800 字符
                return allText.slice(-800) || '(未提取到回复内容)';
            }
        """, text_before)

    def _send_via_rpc(self, page) -> dict:
        """通过 JavaScript 直接调用 dingtalk.net.rpc API"""
        try:
            result = page.evaluate("""
                (async () => {
                    if (!window.dingtalk || !window.dingtalk.net || !window.dingtalk.net.rpc) {
                        return JSON.stringify({error: 'dingtalk.net.rpc 不可用'});
                    }
                    return new Promise((resolve) => {
                        window.dingtalk.net.rpc(
                            '/r/Adaptor/AICopilotI/getCopilotSessionModel',
                            {},
                            JSON.stringify([{corpId: '', scene: 'externalCopilot'}]),
                            function(err, resp) {
                                if (err) {
                                    resolve(JSON.stringify({error: 'getSession failed', details: err}));
                                    return;
                                }
                                let sessionId = '';
                                try {
                                    const data = JSON.parse(resp);
                                    sessionId = data && data.sessionId ? data.sessionId : '';
                                } catch(e) {}
                                if (!sessionId) {
                                    resolve(JSON.stringify({error: 'no sessionId', response: (resp || '').substring(0, 200)}));
                                    return;
                                }
                                window.dingtalk.net.rpc(
                                    '/r/Adaptor/AICopilotI/sendCopilotMsgByUser',
                                    {},
                                    JSON.stringify([{
                                        sessionId: sessionId,
                                        type: 'TEXT',
                                        content: { text: '调用技能更新' },
                                        extension: { ai_robot_sessionid: sessionId }
                                    }, {}]),
                                    function(err2, resp2) {
                                        if (err2) {
                                            resolve(JSON.stringify({error: 'sendMsg failed', details: err2}));
                                        } else {
                                            resolve(JSON.stringify({success: true, response: resp2}));
                                        }
                                    }
                                );
                            }
                        );
                    });
                })()
            """)

            result_data = json.loads(result)
            if result_data.get("success"):
                logger.info("RPC 发送成功")
                return {"success": True, "response": json.dumps(result_data.get("response", "")), "method": "rpc"}
            else:
                logger.warning("RPC 发送失败: %s", result_data.get("error", ""))
                return {"success": False, "response": result_data.get("error", ""), "method": "rpc"}

        except Exception as e:
            logger.warning("RPC 调用异常: %s", e)
            return {"success": False, "response": str(e), "method": "rpc"}


def trigger_knowledge_relearn(headless: bool = True) -> dict:
    """便捷函数：触发 AI 助理知识库更新

    Returns:
        {"success": bool, "response": str, "method": str}
    """
    return RelearnTrigger(headless=headless).trigger()
