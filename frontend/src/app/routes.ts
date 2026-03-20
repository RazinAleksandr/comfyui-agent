import { createBrowserRouter } from "react-router";
import LoginPage from "./pages/LoginPage";
import HomePage from "./pages/HomePage";
import AvatarDetailPage from "./pages/AvatarDetailPage";
import TaskDetailPage from "./pages/TaskDetailPage";
import AuthLayout from "./auth/AuthLayout";
import ProtectedLayout from "./auth/ProtectedLayout";

export const router = createBrowserRouter([
  {
    // AuthProvider wraps all routes
    Component: AuthLayout,
    children: [
      {
        path: "/login",
        Component: LoginPage,
      },
      {
        // Protected routes
        Component: ProtectedLayout,
        children: [
          { path: "/", Component: HomePage },
          { path: "/avatar/:avatarId", Component: AvatarDetailPage },
          { path: "/task/:avatarId/:runId", Component: TaskDetailPage },
        ],
      },
    ],
  },
]);
