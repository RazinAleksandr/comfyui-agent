import { useState } from "react";
import { useNavigate } from "react-router";
import { useForm } from "react-hook-form";
import { motion } from "motion/react";
import { useAuth } from "../auth/AuthContext";

interface LoginForm {
  username: string;
  password: string;
}

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const { register, handleSubmit } = useForm<LoginForm>();

  const onSubmit = async (data: LoginForm) => {
    setError(null);
    setLoading(true);
    try {
      await login(data.username, data.password);
      navigate("/", { replace: true });
    } catch {
      setError("Invalid username or password");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#060a10] flex items-center justify-center">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: "easeOut" }}
        className="w-full max-w-md mx-4"
      >
        <div className="bg-white/[0.04] backdrop-blur-xl border border-white/[0.08] rounded-2xl p-8 shadow-2xl">
          <div className="text-center mb-8">
            <h1 className="text-3xl font-bold text-white mb-2">AI Avatar Studio</h1>
            <p className="text-white/40 text-sm">Sign in to your account</p>
          </div>

          <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-white/60 mb-1.5">Username</label>
              <input
                {...register("username", { required: true })}
                type="text"
                autoComplete="username"
                className="w-full px-4 py-2.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-white placeholder-white/20 focus:outline-none focus:border-sky-400/40 focus:ring-1 focus:ring-sky-400/40 transition-colors"
                placeholder="Enter username"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-white/60 mb-1.5">Password</label>
              <input
                {...register("password", { required: true })}
                type="password"
                autoComplete="current-password"
                className="w-full px-4 py-2.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-white placeholder-white/20 focus:outline-none focus:border-sky-400/40 focus:ring-1 focus:ring-sky-400/40 transition-colors"
                placeholder="Enter password"
              />
            </div>

            {error && (
              <p className="text-red-400 text-sm text-center">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-gradient-to-r from-sky-500 to-blue-600 text-white font-medium rounded-lg hover:from-sky-400 hover:to-blue-500 transition-all disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-sky-500/20"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  Signing in...
                </span>
              ) : (
                "Sign In"
              )}
            </button>
          </form>

          <div className="mt-4">
            <button
              disabled
              className="w-full py-2.5 bg-white/[0.03] border border-white/[0.06] text-white/25 font-medium rounded-lg cursor-not-allowed relative"
            >
              Register
              <span className="absolute -top-2 -right-2 bg-sky-500/15 text-sky-300 text-[10px] font-bold px-1.5 py-0.5 rounded-full border border-sky-400/20">
                Coming Soon
              </span>
            </button>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
