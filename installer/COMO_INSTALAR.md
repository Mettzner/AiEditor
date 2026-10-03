# Como instalar o AiEditor

## Requisitos

- Windows 10 ou 11, 64 bits.
- Cerca de 2 GB livres, mais o espaço dos vídeos e do cache.
- Internet.

Não é preciso instalar Python, Node, FFmpeg nem nada além do instalador.

## Instalação

1. Baixe o arquivo `AiEditor-Setup-<versão>.exe` e dê dois cliques.
2. O Windows pode mostrar **"O Windows protegeu o computador"** (SmartScreen). Isso acontece porque o programa não tem assinatura digital paga. Clique em **Mais informações** e depois em **Executar assim mesmo**.
3. Siga o assistente: Avançar, Avançar, Concluir. Não pede senha de administrador.
4. O AiEditor abre numa janela própria. Na primeira vez, um assistente pede:
   - **Suas chaves de API.** Cada pessoa usa as próprias chaves. O mínimo para começar é a Darkvi (narração) e o Pexels ou o Pixabay (vídeos). Há um link para gerar cada chave e um botão **Testar**.
   - **Sua conta Google** (opcional), para os vídeos irem para o seu Drive. O Google mostra **"Este app não foi verificado"**: clique em **Avançado** e depois em **Acessar AiEditor**.
   - **O modelo de transcrição.** O download é feito uma vez, e o recomendado é o `small`, com uns 480 MB.
   - **O primeiro canal**, com nome, idioma e estilo.

## No dia a dia

- **Atalho:** abra pelo Menu Iniciar ou pela área de trabalho. Abrir de novo com o app aberto só traz a janela para frente.
- **Fechar com produção em andamento:** o app pergunta se deve continuar em segundo plano. Nesse caso ele fica no ícone perto do relógio. Clique com o botão direito no ícone para abrir a janela, abrir a pasta de dados, abrir os logs ou sair.
- **Onde ficam os dados:** os dados (canais, produções, vídeos, cache e logs) ficam em `%LOCALAPPDATA%\AiEditor`. As chaves ficam no Gerenciador de Credenciais do Windows.
- **Atualizar:** quando sair uma versão nova, aparece um aviso no menu lateral. Basta instalar a versão nova por cima, e nada se perde.
- **Desinstalar:** use Configurações do Windows → Aplicativos → AiEditor. O desinstalador pergunta se você quer apagar também os seus dados. O padrão é manter.

## Se algo der errado

- **Antivírus:** se o antivírus bloquear o programa, é um falso positivo comum em programas feitos em Python. Restaure o arquivo e, se puder, envie-o ao fabricante como falso positivo.
- **Problemas em geral:** pelo ícone da bandeja, clique em **Abrir logs** e mande os arquivos `launcher.log`, `server.log` e `worker.log` para quem te passou o instalador.
