import { createBrowserRouter } from "react-router";
import HomePage from "./pages/HomePage";
import AvatarDetailPage from "./pages/AvatarDetailPage";
import TaskDetailPage from "./pages/TaskDetailPage";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: HomePage,
  },
  {
    path: "/avatar/:avatarId",
    Component: AvatarDetailPage,
  },
  {
    path: "/task/:avatarId/:runId",
    Component: TaskDetailPage,
  },
]);
