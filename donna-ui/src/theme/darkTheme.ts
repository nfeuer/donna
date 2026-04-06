import { theme, type ThemeConfig } from "antd";

const darkTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#1890ff",
    colorBgContainer: "#1f1f1f",
    colorBgElevated: "#262626",
    colorBgLayout: "#141414",
    borderRadius: 6,
    fontSize: 13,
  },
  components: {
    Layout: {
      siderBg: "#1a1a2e",
      headerBg: "#1a1a2e",
      bodyBg: "#141414",
    },
    Menu: {
      darkItemBg: "#1a1a2e",
      darkItemSelectedBg: "#16213e",
    },
    Card: {
      colorBgContainer: "#1f1f1f",
    },
    Table: {
      colorBgContainer: "#1f1f1f",
      headerBg: "#262626",
    },
  },
};

export default darkTheme;

// Shared color constants for charts and status indicators
export const STATUS_COLORS: Record<string, string> = {
  SUCCESS: "#52c41a",
  WARNING: "#faad14",
  ERROR: "#ff4d4f",
  INFO: "#1890ff",
};

export const LEVEL_COLORS: Record<string, string> = {
  DEBUG: "#8c8c8c",
  INFO: "#1890ff",
  WARNING: "#faad14",
  ERROR: "#ff4d4f",
  CRITICAL: "#eb2f96",
};

export const CHART_COLORS = [
  "#1890ff",
  "#52c41a",
  "#faad14",
  "#ff4d4f",
  "#722ed1",
  "#13c2c2",
  "#eb2f96",
  "#fa8c16",
];
