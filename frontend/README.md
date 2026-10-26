# Enhanced RAG System Frontend

A production-ready, professional React frontend for the Enhanced RAG System.

## ✨ Features

- 🎨 **Modern Design**: Professional color palette with blue gradients and clean interface
- 💬 **Intuitive Chat Interface**: Real-time messaging with smooth animations
- 📁 **Smart File Management**: Sidebar with file categorization and statistics
- 🎯 **Flexible File Selection**: Dropdown selector + sidebar for easy data source switching
- 📊 **Rich Metadata Display**: Expandable SQL queries, reasoning steps, and file sources
- ⚡ **Production Ready**: Optimized performance with proper loading states
- 📱 **Fully Responsive**: Works seamlessly on desktop, tablet, and mobile
- ♿ **Accessible**: ARIA labels, keyboard navigation, and focus states
- 🎭 **Professional Polish**: Shadows, transitions, and micro-interactions

## 🎨 Design System

- **Primary Color**: Cyan/Sky Blue (#0ea5e9)
- **Typography**: Inter font family
- **Shadows**: Soft, layered shadows for depth
- **Animations**: Smooth transitions and entrance animations
- **Spacing**: Consistent 4px/8px grid system

## 🚀 Quick Start

### Prerequisites

- Node.js 16+
- Backend FastAPI server running on `http://localhost:8000`

### Installation

```bash
cd frontend
npm install
```

### Development

```bash
npm run dev
```

Open `http://localhost:5173` in your browser

### Production Build

```bash
npm run build
npm run preview
```

## 📁 Project Structure

```
frontend/
├── src/
│   ├── App.jsx          # Main application component
│   ├── main.jsx         # React entry point
│   └── index.css        # Global styles and utilities
├── index.html           # HTML template
├── package.json         # Dependencies
├── vite.config.js       # Vite configuration
├── tailwind.config.js   # Tailwind customization
└── postcss.config.js    # PostCSS setup
```

## 🎯 Key Features Explained

### File Management
- **Auto-load**: Files automatically load from the uploads folder on backend startup
- **Smart Selection**: Choose specific files or let AI auto-select the best source
- **File Types**: Supports databases, structured data (CSV/Excel), and documents (PDF/DOCX/PPTX)

### Chat Interface
- **Auto-resize**: Input field grows as you type
- **Keyboard Shortcuts**: Enter to send, Shift+Enter for new line
- **Loading States**: Visual feedback during processing
- **Error Handling**: Graceful error messages

### Response Display
- **Expandable Sections**: Collapsible SQL queries and reasoning steps
- **Source Attribution**: See which files were used for each answer
- **Status Badges**: Visual indicators for cache hits, retries, and methods
- **Code Highlighting**: Syntax-highlighted SQL queries

## 🛠️ Tech Stack

- **React 18**: Modern React with hooks
- **Vite**: Lightning-fast build tool
- **Tailwind CSS**: Utility-first styling
- **Lucide React**: Beautiful, consistent icons
- **Inter Font**: Professional typography

## 🎨 Customization

### Colors

Edit `tailwind.config.js` to change the color palette:

```js
colors: {
  primary: { /* Your brand colors */ },
  neutral: { /* Gray scale */ },
}
```

### Fonts

Replace Inter font in `index.css`:

```css
@import url('https://fonts.googleapis.com/css2?family=YourFont:wght@400;500;600;700&display=swap');
```

## 📝 API Integration

The frontend connects to these backend endpoints:

- `GET /api/files` - List all loaded files
- `POST /api/query` - Submit a query
- `DELETE /api/context/clear` - Clear conversation
- `DELETE /api/files/clear` - Reload files

## 🐛 Troubleshooting

### Backend Connection Issues
- Ensure backend is running on `http://localhost:8000`
- Check CORS settings in backend `main.py`

### Files Not Loading
- Verify files are in the `uploads` folder
- Check backend logs for file loading errors
- Ensure file formats are supported

### Build Issues
- Clear node_modules: `rm -rf node_modules && npm install`
- Clear Vite cache: `rm -rf .vite`

## 📄 License

MIT

## 🤝 Contributing

Contributions welcome! Please follow the existing code style and add tests for new features.

---

Made with ❤️ for intelligent document analysis
