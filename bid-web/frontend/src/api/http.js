import axios from 'axios';
import { ElMessage } from 'element-plus';

// 超时 30 分钟：扫描件 PDF 走本地 CPU OCR 时较慢（实测约 7s/页）
const http = axios.create({ baseURL: '/api', timeout: 1800000 });

http.interceptors.response.use(
	(res) => res.data,
	(err) => {
		const msg = err.response?.data?.error || err.message || '请求失败';
		ElMessage.error(msg);
		return Promise.reject(err);
	}
);

export default http;
