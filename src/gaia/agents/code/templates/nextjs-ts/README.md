# Next.js Full-Stack Application

This is a full-stack Next.js application template with TypeScript, Prisma ORM, and SQLite.

## Features

- **Next.js 14+** with App Router
- **TypeScript** for type safety
- **Prisma ORM** for database operations
- **SQLite** for embedded database (zero installation)
- **Tailwind CSS** for styling
- **ESLint** for code quality

## Getting Started

### 1. Install Dependencies

```bash
npm install
```

### 2. Set Up Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

### 3. Set Up Database

Generate Prisma Client and push the database schema:

```bash
npm run db:generate
npm run db:push
```

This will create the SQLite database file and generate the Prisma Client.

### 4. Run Development Server

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Project Structure

```
├── src/
│   ├── app/              # Next.js App Router pages
│   │   ├── layout.tsx    # Root layout
│   │   ├── page.tsx      # Homepage
│   │   ├── globals.css   # Global styles
│   │   └── api/          # API routes
│   └── lib/              # Utility functions
│       └── prisma.ts     # Prisma client
├── prisma/               # Prisma schema and migrations
│   └── schema.prisma     # Database schema definitions
├── public/               # Static assets
└── dev.db                # SQLite database file (auto-generated)
```

## Available Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm start` - Start production server
- `npm run lint` - Run ESLint
- `npm run db:generate` - Generate Prisma Client
- `npm run db:push` - Push schema changes to database
- `npm run db:migrate` - Create and apply migrations
- `npm run db:studio` - Open Prisma Studio (database GUI)

## Database Schema

The template includes example models:

- **User** - User accounts with name and email
- **Post** - Blog posts with title, content, and user reference

Modify `prisma/schema.prisma` to customize your schema, then run `npm run db:generate` and `npm run db:push` to apply changes.

## API Routes

Example health check endpoint:

```
GET /api/health
```

Returns server and database status.

## Learn More

- [Next.js Documentation](https://nextjs.org/docs)
- [Prisma Documentation](https://www.prisma.io/docs)
- [Tailwind CSS Documentation](https://tailwindcss.com/docs)
