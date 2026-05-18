import axios from "axios";
import { toast } from "sonner";

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "",
  timeout: 60000,
});

client.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || error.message;

    if (!error.response) {
      toast.warning("Network Error", {
        description: "Could not reach the Donna API. Is the backend running?",
      });
    } else if (status === 401 || status === 403) {
      toast.error("Authentication Required", { description: detail });
    } else if (status && status >= 400) {
      toast.error(`Error (${status})`, { description: detail });
    }

    return Promise.reject(error);
  },
);

export default client;
