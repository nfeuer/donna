import axios from "axios";
import { toast } from "sonner";

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
      toast.error(`Server Error (${status})`, { description: detail });
    } else if (!error.response) {
      toast.warning("Network Error", {
        description: "Could not reach the Donna API. Is the backend running?",
      });
    }

    return Promise.reject(error);
  },
);

export default client;
