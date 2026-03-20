>  **Nota**: Se você precisa utilizar a funcionalidade de exportação PDF, instale as dependências do sistema seguindo os passos abaixo. Se não precisar da exportação PDF, pode pular esta etapa; as demais funcionalidades do sistema não serão afetadas.

<details>
<summary><b> Passos de Instalação para Windows</b></summary>

```powershell
# 1. Baixe e instale o GTK3 Runtime (execute na máquina host)
# Acesse: https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases
# Baixe o arquivo .exe mais recente e instale
# É fortemente recomendado instalar no caminho padrão, pois isso pode ajudar a evitar diversos erros imprevistos.

# 2. Adicione o diretório bin da instalação GTK ao PATH (abra um novo terminal depois)
# Exemplo de caminho padrão (substitua pelo seu caminho de instalação personalizado, se diferente)
set PATH=C:\Program Files\GTK3-Runtime Win64\bin;%PATH%

# Opcional: persistir a configuração
setx PATH "C:\Program Files\GTK3-Runtime Win64\bin;%PATH%"

# Se instalado em um caminho personalizado, substitua pelo caminho real, ou defina GTK_BIN_PATH=<seu-caminho-bin> e reabra o terminal

# 3. Verifique em um novo terminal
python -m ReportEngine.utils.dependency_check
# Você deverá ver "✓ Pango dependency check passed"
```

</details>

<details>
<summary><b> Passos de Instalação para macOS</b></summary>

```bash
# 1. Instale as dependências do sistema (execute na máquina host)
brew install pango gdk-pixbuf libffi

# 2. Configure a variável de ambiente (obrigatório)
# Apple Silicon
export DYLD_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_LIBRARY_PATH
# Intel Mac
export DYLD_LIBRARY_PATH=/usr/local/lib:$DYLD_LIBRARY_PATH

# Ou adicione permanentemente ao ~/.zshrc
echo 'export DYLD_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_LIBRARY_PATH' >> ~/.zshrc
# Usuários Intel: echo 'export DYLD_LIBRARY_PATH=/usr/local/lib:$DYLD_LIBRARY_PATH' >> ~/.zshrc
source ~/.zshrc

# 3. Verifique em um novo terminal
python -m ReportEngine.utils.dependency_check
# Você deverá ver "✓ Pango dependency check passed"
```

</details>

<details>
<summary><b> Passos de Instalação para Ubuntu/Debian</b></summary>

```bash
# 1. Instale as dependências do sistema (execute na máquina host)
sudo apt-get update
sudo apt-get install -y \
  libpango-1.0-0 \
  libpangoft2-1.0-0 \
  libffi-dev \
  libcairo2

# Priorizar o nome de pacote mais recente; fazer fallback se o repositório não o fornecer
if sudo apt-cache show libgdk-pixbuf-2.0-0 >/dev/null 2>&1; then
  sudo apt-get install -y libgdk-pixbuf-2.0-0
else
  sudo apt-get install -y libgdk-pixbuf2.0-0
fi
```

</details>

<details>
<summary><b> Passos de Instalação para CentOS/RHEL</b></summary>

```bash
# 1. Instale as dependências do sistema (execute na máquina host)
sudo yum install -y pango gdk-pixbuf2 libffi-devel cairo
```

</details>


>  **Dica**: Se estiver utilizando deploy com Docker, não é necessário instalar essas dependências manualmente; a imagem Docker já contém todas as dependências de sistema necessárias.
