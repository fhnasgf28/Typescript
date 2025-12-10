# One Day One Code — Pembelajaran TypeScript

Selamat datang di repo pembelajaran TypeScript! Proyek ini mengusung prinsip "One Day One Code": setiap hari kita menyelesaikan satu latihan / eksperimen kecil untuk membangun kebiasaan, memahami konsep, dan mengecek kemajuan secara konsisten.

Tujuan
- Membangun kebiasaan menulis TypeScript setiap hari.
- Mempelajari konsep dasar hingga lanjutan secara bertahap.
- Mempunyai portofolio kecil berupa kumpulan latihan harian.

Filosofi "One Day One Code"
- Konsistensi lebih penting dari durasi: cukup 30–60 menit setiap hari.
- Fokus pada praktik kecil yang dapat diselesaikan dalam satu sesi.
- Catat pembelajaran dan kesulitan setiap hari agar proses belajar dapat ditingkatkan.

Struktur Repo (disarankan)
- /day-01
  - solution.ts
  - README.md (catatan singkat: tujuan, sumber, apa yang dipelajari)
- /day-02
- ...
- README.md (file ini)

Contoh struktur file per hari
```
day-01/
  ├─ solution.ts
  └─ README.md
```

Cara Memulai (lokal)
1. Pastikan Node.js + npm terpasang.
2. Inisialisasi project:
```bash
npm init -y
npm install --save-dev typescript ts-node @types/node
npx tsc --init
```
3. Menjalankan file TypeScript cepat:
```bash
npx ts-node day-01/solution.ts
```

Saran konfigurasi tsconfig (awal)
- Target ES2019 atau lebih tinggi
- Module commonjs atau esnext sesuai kebutuhan
- "strict": true (disarankan untuk belajar tipe dengan baik)

Template Halaman Harian (day-xx/README.md)
- Judul latihan
- Durasi yang dihabiskan
- Tujuan pembelajaran
- Langkah yang dilakukan (ringkas)
- Kesulitan & catatan
- Link referensi / sumber

Contoh commit message
- "day-01: belajar basic types dan function signatures"
- "day-12: implementasi generic util untuk array"

Tips Belajar
- Fokus pada satu konsep per hari (mis. tipe dasar, interface, union, generic, mapped types, conditional types, decorators).
- Gunakan Playground TypeScript untuk eksperimen cepat.
- Tulis komentar singkat tentang kenapa memilih solusi itu.
- Kembali dan refactor setelah mempelajari teknik baru.

Sumber Referensi
- Official: https://www.typescriptlang.org/
- Buku / artikel: cari materi tentang "TypeScript Deep Dive", "Advanced TypeScript Types"
- Playground: https://www.typescriptlang.org/play

Kontribusi
- Tambahkan folder day-XX sesuai urutan.
- Sertakan README singkat untuk tiap hari.
- Gunakan branch terpisah untuk PR besar, atau commit langsung jika ini repo belajar pribadi.

Lisensi
- MIT (atau pilih lisensi yang diinginkan)

Selamat belajar — ingat konsistensi! Jika ingin, kita bisa:
- menambahkan templates otomatis (script) untuk membuat folder day-XX,
- atau saya bantu push file README ini langsung ke repo.
