"""钉钉 API 客户端

封装钉钉开放平台的知识库相关 API 调用。
"""
import time
import logging
import requests

logger = logging.getLogger(__name__)

OAPI_BASE = "https://oapi.dingtalk.com"
API_BASE = "https://api.dingtalk.com"

# Token 过期前 5 分钟刷新
TOKEN_REFRESH_MARGIN = 5 * 60


class DingTalkClient:
    """钉钉 API 客户端"""

    def __init__(self, app_key: str, app_secret: str, union_id: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self.union_id = union_id
        self._token = None
        self._token_expires_at = 0

    # ----------------------------------------------------------------
    # Token 管理
    # ----------------------------------------------------------------
    def _get_access_token(self) -> str:
        """获取企业内部应用 access_token，带缓存自动刷新"""
        if self._token and time.time() < self._token_expires_at - TOKEN_REFRESH_MARGIN:
            return self._token

        url = f"{OAPI_BASE}/gettoken"
        resp = requests.get(url, params={
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        })
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"获取 access_token 失败: {data.get('errmsg')}")
        self._token = data["access_token"]
        self._token_expires_at = time.time() + data["expires_in"]
        logger.info("已刷新钉钉 access_token")
        return self._token

    def _build_headers(self) -> dict:
        """构建 API 请求头"""
        return {
            "x-acs-dingtalk-access-token": self._get_access_token(),
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict = None) -> dict:
        """发起 GET 请求（新版 API）"""
        if params is None:
            params = {}
        params["operatorId"] = self.union_id
        url = f"{API_BASE}{path}"
        resp = requests.get(url, headers=self._build_headers(), params=params)
        data = resp.json()
        err = self._check_error(data)
        if err:
            raise RuntimeError(f"钉钉 GET {path} 失败: {err}")
        return data

    def _post(self, path: str, body: dict = None) -> dict:
        """发起 POST 请求（新版 API），自动注入 operatorId"""
        if body is None:
            body = {}
        body["operatorId"] = self.union_id
        params = {"operatorId": self.union_id}
        url = f"{API_BASE}{path}"
        resp = requests.post(url, headers=self._build_headers(), params=params, json=body)
        data = resp.json()
        err = self._check_error(data)
        if err:
            raise RuntimeError(f"钉钉 POST {path} 失败: {err}")
        return data

    @staticmethod
    def _check_error(data: dict) -> str | None:
        """检查 API 响应中是否有错误，返回错误描述或 None"""
        # 新版 API 错误格式
        code = data.get("code")
        if code and code not in ("0", 0, None, ""):
            msg = data.get("message", "未知错误")
            # 忽略 MissingoperatorId 的干扰
            if code != "MissingoperatorId":
                return f"[{code}] {msg}"
        # 旧版 API 错误格式
        errcode = data.get("errcode")
        if errcode and errcode != 0:
            return f"[{errcode}] {data.get('errmsg', '未知错误')}"
        return None

    # ----------------------------------------------------------------
    # 用户相关
    # ----------------------------------------------------------------
    def get_user_detail(self, userid: str) -> dict:
        """查询用户详情（旧版 oapi 接口）"""
        token = self._get_access_token()
        url = f"{OAPI_BASE}/topapi/v2/user/get"
        resp = requests.post(url, params={"access_token": token}, json={"userid": userid})
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"查询用户详情失败: {data.get('errmsg')}")
        return data["result"]

    # ----------------------------------------------------------------
    # 知识库（workspace）相关
    # ----------------------------------------------------------------
    def list_workspaces(self, max_results: int = 50) -> list[dict]:
        """获取当前用户有权限的知识库列表"""
        data = self._get("/v2.0/wiki/workspaces", {
            "maxResults": str(max_results),
        })
        return data.get("workspaces", [])

    def list_nodes(self, parent_node_id: str, max_results: int = 50) -> list[dict]:
        """获取指定父节点下的子节点列表"""
        data = self._get("/v2.0/wiki/nodes", {
            "parentNodeId": parent_node_id,
            "maxResults": str(max_results),
        })
        return data.get("nodes", [])

    # ----------------------------------------------------------------
    # 文档操作
    # ----------------------------------------------------------------
    def create_document(self, workspace_id: str, parent_node_id: str,
                        name: str, doc_type: str = "DOC") -> dict:
        """在知识库中创建新文档"""
        return self._post(f"/v1.0/doc/workspaces/{workspace_id}/docs", {
            "name": name,
            "docType": doc_type,
            "parentNodeId": parent_node_id,
        })

    def create_folder(self, workspace_id: str, parent_node_id: str,
                      name: str) -> dict:
        """在知识库中创建文件夹（同创建文档接口，docType=FOLDER）"""
        return self._post(f"/v1.0/doc/workspaces/{workspace_id}/docs", {
            "name": name,
            "docType": "FOLDER",
            "parentNodeId": parent_node_id,
        })

    def find_or_create_folder(self, workspace_id: str, parent_node_id: str,
                              name: str) -> str:
        """查找或创建文件夹，返回文件夹的 nodeId"""
        nodes = self.list_nodes(parent_node_id)
        for node in nodes:
            if node.get("name") == name:
                node_id = node.get("nodeId", "")
                logger.info("找到已有文件夹: %s (nodeId=%s)", name, node_id)
                return node_id
        # 未找到，创建
        folder = self.create_folder(
            workspace_id=workspace_id,
            parent_node_id=parent_node_id,
            name=name,
        )
        node_id = folder.get("nodeId", "")
        logger.info("已创建文件夹: %s (nodeId=%s)", name, node_id)
        return node_id

    def overwrite_content(self, doc_key: str, content: str,
                          content_type: str = "markdown"):
        """覆盖写入文档内容"""
        return self._post(f"/v1.0/doc/suites/documents/{doc_key}/overwriteContent", {
            "content": content,
            "contentType": content_type,
        })

    def delete_document(self, workspace_id: str, node_id: str):
        """删除知识库文档

        通过节点的 nodeId（不是 docKey）删除文档。
        nodeId 可以从文档 URL 中获取：https://alidocs.dingtalk.com/i/nodes/{nodeId}

        Args:
            workspace_id: 知识库 workspaceId
            node_id: 文档的 nodeId（URL 中的节点ID）
        """
        url = f"{API_BASE}/v1.0/doc/workspaces/{workspace_id}/docs/{node_id}"
        resp = requests.delete(
            url,
            headers=self._build_headers(),
            params={"operatorId": self.union_id},
        )
        if resp.status_code != 200:
            err = resp.json()
            raise RuntimeError(f"删除文档失败: [{err.get('code')}] {err.get('message')}")
        logger.info("文档已删除: workspace=%s, nodeId=%s", workspace_id, node_id)
        return True
