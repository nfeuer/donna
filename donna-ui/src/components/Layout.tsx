import { useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { Layout, Menu, Typography } from "antd";
import {
  DashboardOutlined,
  FileSearchOutlined,
  CheckSquareOutlined,
  RobotOutlined,
  SettingOutlined,
  FileTextOutlined,
  ExperimentOutlined,
  BulbOutlined,
} from "@ant-design/icons";

const { Sider, Header, Content } = Layout;
const { Title } = Typography;

const NAV_ITEMS = [
  { key: "/", icon: <DashboardOutlined />, label: "Dashboard" },
  { key: "/logs", icon: <FileSearchOutlined />, label: "Logs" },
  { key: "/tasks", icon: <CheckSquareOutlined />, label: "Tasks" },
  { key: "/agents", icon: <RobotOutlined />, label: "Agents" },
  { key: "/configs", icon: <SettingOutlined />, label: "Configs" },
  { key: "/prompts", icon: <FileTextOutlined />, label: "Prompts" },
  { key: "/shadow", icon: <ExperimentOutlined />, label: "Shadow Scoring" },
  { key: "/preferences", icon: <BulbOutlined />, label: "Preferences" },
];

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        width={220}
        style={{ borderRight: "1px solid #303030" }}
      >
        <div
          style={{
            padding: collapsed ? "16px 8px" : "16px 20px",
            textAlign: collapsed ? "center" : "left",
          }}
        >
          <Title
            level={4}
            style={{ color: "#fff", margin: 0, whiteSpace: "nowrap" }}
          >
            {collapsed ? "D" : "Donna"}
          </Title>
          {!collapsed && (
            <span style={{ color: "#8c8c8c", fontSize: 11 }}>
              Management GUI
            </span>
          )}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={NAV_ITEMS}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            padding: "0 24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderBottom: "1px solid #303030",
          }}
        >
          <span style={{ color: "#8c8c8c", fontSize: 13 }}>
            {NAV_ITEMS.find((i) => i.key === location.pathname)?.label ??
              "Donna Management"}
          </span>
        </Header>
        <Content style={{ padding: 24, overflow: "auto" }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
