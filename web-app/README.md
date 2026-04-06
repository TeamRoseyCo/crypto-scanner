# Ankh Countdown Timer

A Next.js web application featuring a countdown timer for the Ankh project.

## Getting Started

### Development Server

```bash
npm install
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) to view the application.

### Editing

Edit `app/page.js` to modify the main page. The page auto-updates as you save changes.

## Features

- Responsive countdown timer
- Next.js App Router
- Optimized fonts with [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts)
- Tailwind CSS styling

## Project Structure

```
web-app/
├── app/           # Next.js app directory
├── public/        # Static assets
├── styles/        # Global styles
├── component/     # React components
├── config/        # Configuration files
└── package.json   # Dependencies
```

## Deployment

### Vercel (Recommended)

1. Push this repository to GitHub
2. Visit [Vercel](https://vercel.com)
3. Click "Add New Project"
4. Connect your GitHub repository
5. Select the main branch
6. Set the root directory to `web-app`
7. Keep the default build settings (auto-detects Next.js)
8. Click "Deploy"

The site will be automatically deployed with a production URL.

### Other Platforms

This is a standard Next.js application and can be deployed to any platform that supports Next.js:
- Netlify
- AWS Amplify
- DigitalOcean App Platform
- Railway

## Environment Variables

No additional environment variables required for basic deployment.

## Learn More

- [Next.js Documentation](https://nextjs.org/docs)
- [Learn Next.js](https://nextjs.org/learn)
- [Next.js GitHub Repository](https://github.com/vercel/next.js)

## Build for Production

```bash
npm run build
npm start
```

## License

Part of the crypto-scanner project - All rights reserved.
