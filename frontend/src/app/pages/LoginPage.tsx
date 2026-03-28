import { useState } from "react";
import { useNavigate } from "react-router";
import { useForm } from "react-hook-form";
import { motion, AnimatePresence } from "motion/react";
import { useAuth } from "../auth/AuthContext";
import { LogIn, X } from "lucide-react";

interface LoginForm {
  username: string;
  password: string;
}

function LoginDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
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
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
            onClick={onClose}
          />

          {/* Dialog */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: -10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: -10 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
            className="fixed z-50 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-sm"
          >
            <div className="bg-[#0c0f1a]/95 backdrop-blur-2xl border border-white/[0.08] rounded-2xl p-7 shadow-2xl shadow-purple-500/10">
              {/* Close button */}
              <button
                onClick={onClose}
                className="absolute top-4 right-4 text-white/30 hover:text-white/60 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>

              <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-white/50 mb-1.5 uppercase tracking-wider">
                    Username
                  </label>
                  <input
                    {...register("username", { required: true })}
                    type="text"
                    autoComplete="username"
                    autoFocus
                    className="w-full px-4 py-2.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-white placeholder-white/20 focus:outline-none focus:border-purple-400/40 focus:ring-1 focus:ring-purple-400/30 transition-colors text-sm"
                    placeholder="Enter username"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-white/50 mb-1.5 uppercase tracking-wider">
                    Password
                  </label>
                  <input
                    {...register("password", { required: true })}
                    type="password"
                    autoComplete="current-password"
                    className="w-full px-4 py-2.5 bg-white/[0.04] border border-white/[0.08] rounded-lg text-white placeholder-white/20 focus:outline-none focus:border-purple-400/40 focus:ring-1 focus:ring-purple-400/30 transition-colors text-sm"
                    placeholder="Enter password"
                  />
                </div>

                {error && (
                  <p className="text-red-400 text-xs text-center">{error}</p>
                )}

                <button
                  type="submit"
                  disabled={loading}
                  className="w-full py-2.5 bg-gradient-to-r from-purple-500 to-pink-500 text-white font-medium rounded-lg hover:from-purple-400 hover:to-pink-400 transition-all disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-purple-500/20 text-sm"
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

                <button
                  type="button"
                  disabled
                  className="w-full py-2.5 bg-white/[0.02] border border-white/[0.05] text-white/20 font-medium rounded-lg cursor-not-allowed relative text-sm"
                >
                  Register
                  <span className="absolute -top-2 -right-2 bg-purple-500/15 text-purple-300 text-[9px] font-bold px-1.5 py-0.5 rounded-full border border-purple-400/20">
                    Soon
                  </span>
                </button>
              </form>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

export default function LoginPage() {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [showLogin, setShowLogin] = useState(false);

  // If already authenticated, go to app
  if (isAuthenticated) {
    navigate("/", { replace: true });
    return null;
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-black via-purple-950/40 to-black overflow-hidden relative">
      {/* Animated background grid */}
      <div className="absolute inset-0 opacity-[0.12]">
        <div
          className="absolute inset-0"
          style={{
            backgroundImage:
              "linear-gradient(rgba(168, 85, 247, 0.5) 1px, transparent 1px), linear-gradient(90deg, rgba(168, 85, 247, 0.5) 1px, transparent 1px)",
            backgroundSize: "60px 60px",
          }}
        />
      </div>

      {/* Ambient glow blobs */}
      <div className="absolute top-1/4 left-1/4 w-[500px] h-[500px] bg-purple-600/20 rounded-full blur-[120px]" />
      <div className="absolute bottom-1/4 right-1/4 w-[400px] h-[400px] bg-pink-600/15 rounded-full blur-[120px]" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[300px] h-[300px] bg-blue-600/10 rounded-full blur-[100px]" />

      {/* Top bar */}
      <header className="relative z-20 flex items-center justify-end px-6 py-5">
        <button
          onClick={() => setShowLogin(true)}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white/90 hover:text-white bg-white/[0.06] hover:bg-white/[0.12] border border-white/[0.12] hover:border-white/[0.2] rounded-lg backdrop-blur-sm transition-all"
        >
          <LogIn className="w-4 h-4" />
          Sign In
        </button>
      </header>

      {/* Login dialog */}
      <LoginDialog open={showLogin} onClose={() => setShowLogin(false)} />

      {/* Hero content */}
      <main className="relative z-10 flex flex-col items-center justify-center min-h-[calc(100vh-80px)] -mt-20">
        <div className="flex flex-col items-center gap-12">
          {/* Logo */}
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.8, ease: "easeOut" }}
            className="relative"
          >
            {/* Glow */}
            <div className="absolute inset-0 blur-3xl opacity-50">
              <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-purple-500 rounded-full" />
              <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-72 h-72 bg-pink-500 rounded-full" />
              <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-56 h-56 bg-blue-500 rounded-full" />
            </div>

            <svg
              width="360"
              height="360"
              viewBox="0 0 500 500"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              className="relative z-10"
            >
              <circle cx="250" cy="250" r="100" fill="url(#hubGrad)" stroke="url(#neonS)" strokeWidth="4" opacity="0.3" />
              <circle cx="250" cy="250" r="120" fill="none" stroke="url(#neonS)" strokeWidth="2" strokeDasharray="10 5" opacity="0.6">
                <animateTransform attributeName="transform" type="rotate" from="0 250 250" to="360 250 250" dur="20s" repeatCount="indefinite" />
              </circle>
              <g transform="translate(250, 250)">
                <circle r="35" fill="url(#coreG)" opacity="0.8">
                  <animate attributeName="r" values="35;40;35" dur="2s" repeatCount="indefinite" />
                  <animate attributeName="opacity" values="0.8;1;0.8" dur="2s" repeatCount="indefinite" />
                </circle>
                <circle r="25" fill="none" stroke="#fff" strokeWidth="2" />
                <circle cx="-10" cy="-8" r="3" fill="#fff" />
                <circle cx="10" cy="-8" r="3" fill="#fff" />
                <circle cx="0" cy="0" r="3" fill="#fff" />
                <circle cx="-8" cy="10" r="3" fill="#fff" />
                <circle cx="8" cy="10" r="3" fill="#fff" />
                <line x1="-10" y1="-8" x2="0" y2="0" stroke="#fff" strokeWidth="1.5" opacity="0.6" />
                <line x1="10" y1="-8" x2="0" y2="0" stroke="#fff" strokeWidth="1.5" opacity="0.6" />
                <line x1="0" y1="0" x2="-8" y2="10" stroke="#fff" strokeWidth="1.5" opacity="0.6" />
                <line x1="0" y1="0" x2="8" y2="10" stroke="#fff" strokeWidth="1.5" opacity="0.6" />
                <line x1="-10" y1="-8" x2="-8" y2="10" stroke="#fff" strokeWidth="1.5" opacity="0.6" />
                <line x1="10" y1="-8" x2="8" y2="10" stroke="#fff" strokeWidth="1.5" opacity="0.6" />
                <circle r="30" fill="none" stroke="#fff" strokeWidth="1" strokeDasharray="3 3" opacity="0.4">
                  <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="10s" repeatCount="indefinite" />
                </circle>
              </g>
              <defs>
                <linearGradient id="hubGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#8B5CF6" />
                  <stop offset="100%" stopColor="#EC4899" />
                </linearGradient>
                <linearGradient id="neonS" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#a78bfa" />
                  <stop offset="50%" stopColor="#ec4899" />
                  <stop offset="100%" stopColor="#60a5fa" />
                </linearGradient>
                <linearGradient id="coreG" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#a78bfa" />
                  <stop offset="50%" stopColor="#ec4899" />
                  <stop offset="100%" stopColor="#60a5fa" />
                </linearGradient>
              </defs>
            </svg>
          </motion.div>

          {/* Text */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.3, ease: "easeOut" }}
            className="text-center space-y-5 max-w-3xl px-6"
          >
            <h1 className="text-6xl sm:text-7xl font-black text-transparent bg-clip-text bg-gradient-to-r from-purple-400 via-pink-400 to-blue-400 leading-tight">
              AI AVATAR FACTORY
            </h1>
            <p className="text-xl sm:text-2xl text-purple-200 leading-relaxed">
              Mass produce viral content with AI human avatars
            </p>
          </motion.div>

          {/* Platform indicators */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.6 }}
            className="flex gap-6 items-center text-sm text-purple-200/90"
          >
            <div className="flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-pink-500 rounded-full animate-pulse" />
              <span>TikTok</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-purple-500 rounded-full animate-pulse" />
              <span>Instagram</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-pulse" />
              <span>Reels</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-1.5 h-1.5 bg-red-500 rounded-full animate-pulse" />
              <span>Shorts</span>
            </div>
          </motion.div>

          {/* Feature pills */}
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.8 }}
            className="flex justify-center gap-3"
          >
            {["Trend Analysis", "AI Video Generation", "Multi-Platform", "GPU Cloud Rendering", "Auto Captioning"].map((f) => (
              <span
                key={f}
                className="px-3.5 py-1.5 text-xs font-medium text-purple-200/70 bg-white/[0.03] border border-white/[0.06] rounded-full"
              >
                {f}
              </span>
            ))}
          </motion.div>

          {/* Hero sign-in CTA */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 1.0 }}
            className="flex flex-col items-center mt-2"
          >
            <button
              onClick={() => setShowLogin(true)}
              className="flex items-center gap-2.5 px-8 py-4 rounded-2xl text-base font-semibold text-white bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-400 hover:to-pink-400 shadow-lg shadow-purple-500/30 transition-all"
            >
              <LogIn className="w-5 h-5" />
              Sign In
            </button>
            <p className="text-sm text-purple-300/50 mt-3">
              Already using the platform? Sign in to your workspace.
            </p>
          </motion.div>
        </div>
      </main>

      {/* Footer */}
      <footer className="relative z-10 text-center pb-6">
        <p className="text-xs text-white/15">AI Avatar Factory</p>
      </footer>
    </div>
  );
}
