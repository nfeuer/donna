import axios from "axios";
import { notification } from "antd";

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "",
  timeout: 15000,
});

client.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || error.message;

    if (status && status >= 500) {
      notification.error({
        message: `Server Error (${status})`,
        description: detail,
        duration: 5,
      });
    } else if (!error.response) {
      notification.warning({
        message: "Network Error",
        description: "Could not reach the Donna API. Is the backend running?",
        duration: 5,
      });
    }

    return Promise.reject(error);
  },
);

export default client;
