import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Toaster, toast } from "sonner";
import { Button, Card, Input } from "@/components/ui";
import { login } from "@/lib/auth";

export default function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setIsSubmitting(true);
    try {
      await login(username, password);
      navigate("/");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <main className="mx-auto flex min-h-screen max-w-sm items-center px-6">
        <Card className="w-full space-y-5">
          <div className="space-y-1">
            <h1 className="text-xl font-medium tracking-tight">Agent Eval Orchestrator</h1>
            <p className="text-sm text-muted-foreground">登录后继续</p>
          </div>
          <form className="space-y-3" onSubmit={onSubmit}>
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="用户名"
              autoComplete="username"
            />
            <Input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="密码"
              type="password"
              autoComplete="current-password"
            />
            <Button className="w-full" disabled={isSubmitting || !username || !password}>
              {isSubmitting ? "登录中" : "登录"}
            </Button>
          </form>
        </Card>
      </main>
      <Toaster position="top-right" style={{ fontFamily: "var(--font-sans)" }} />
    </div>
  );
}
