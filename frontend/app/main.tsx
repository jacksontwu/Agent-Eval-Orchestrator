import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./app.css";
import Root from "./root";
import TasksPage from "./routes/tasks";
import CreatePage from "./routes/create";
import TaskDetailPage from "./routes/task-detail";
import WorkersPage from "./routes/workers";
import { getToken } from "@/lib/api";

// Persist a ?token= param on first load.
getToken();

const router = createBrowserRouter([
  {
    path: "/",
    element: <Root />,
    children: [
      { index: true, element: <TasksPage /> },
      { path: "create", element: <CreatePage /> },
      { path: "tasks/:runId", element: <TaskDetailPage /> },
      { path: "workers", element: <WorkersPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
