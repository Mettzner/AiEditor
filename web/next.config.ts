import type { NextConfig } from "next";

// Produção: export estático (web/out), servido pelo próprio FastAPI no app instalado: sem Node na máquina do usuário.
// Desenvolvimento: `next dev` continua igual e chama a API em http://localhost:8000/api.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
};

export default nextConfig;
