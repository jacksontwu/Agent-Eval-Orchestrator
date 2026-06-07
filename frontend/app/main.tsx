import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./app.css";
import Root from "./root";
import TasksPage from "./routes/tasks";
import CreatePage from "./routes/create";
import TaskDetailPage from "./routes/task-detail";
import WorkersPage from "./routes/workers";
import LoginPage from "./routes/login";
import UsersPage from "./routes/users";
import GroupsPage from "./routes/groups";

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <Root />,
    children: [
      { index: true, element: <TasksPage /> },
      { path: "create", element: <CreatePage /> },
      { path: "tasks/:runId", element: <TaskDetailPage /> },
      { path: "workers", element: <WorkersPage /> },
      { path: "users", element: <UsersPage /> },
      { path: "groups", element: <GroupsPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
