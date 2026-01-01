enum KategoriPengeluaran {
    Makanan = "Makanan",
    Transportasi = "Transportasi",
    SewaCicilan = "Sewa/cicilan",
    Tagihan = "Tagihana (listrik, air,internet)",
    Hiburan = "Hiburan",
    Edukasi = "Edukasi",
    LainLain = "Lain-lain",
}

// interface mendefinisikan bentuk atau struktur dari sebuah objek.
interface Pengeluaran {
    deskripsi: string;
    jumlah: number;
    kategori: KategoriPengeluaran;
    tanggal?: Date;
}

class PelacakPengeluaran {
    private gajiBulanan: number;
    private daftarPengeluaran: Pengeluaran[];

    constructor(gaji: number) {
        this.gajiBulanan = gaji;
        this.daftarPengeluaran = [];
        console.log(`Pelack pengeluaran harus lebih dari nol: ${this.formatRupiah(this.gajiBulanan)}`);
        return;
    }
    tambahPengeluaran(deskripsi: string, jumlah: number, kategori:KategoriPengeluaran, tanggal?:Date): void {
        if (jumlah <=0) {
            console.warn("jumlah pengeluaran harus lebih besar dari 0")
            return;
        }
        const newExpense: Pengeluaran = {
            deskripsi,
            jumlah,
            kategori,
            tanggal: tanggal || new Date()
        };
        this.daftarPengeluaran.push(newExpense);
        console.log(`ditambahkan '${deskripsi}' (${this.formatRupiah(jumlah)}) di kategori ${kategori}.`)
    }

    getTotalPengeluaran(): number {
        return this.daftarPengeluaran.reduce((total, pengeluaran) => total + pengeluaran.jumlah, 0)
    }

    getSisaSaldo(): number {
        return this.gajiBulanan - this.getTotalPengeluaran();
    }

    tampilkanRingkasa(): void{
        
    }

    // helper mthod untuk memformat angka menjadi pengeluaran
    private formatRupiah(angka: number): string {
        return new Intl.NumberFormat('id-ID', {
            style: 'currency',
            currency: 'IDR',
            minimumFractionDigits:0,
            maximumFractionDigits:0
        }).format(angka)
    }
}