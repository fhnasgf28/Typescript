// mendifinisikan bentuk data (interface)
//membuat aturan setiap product harus punya bentuk seperti ini

interface Product {
    id: number;
    name: string;
    price: number;
    isActive: boolean;
    description?: string;
}

// 2 membuat variabel dengan tipe data
//kita buat array yang isinya wajib mengikuti aturan Product
const cart: Product[] = [
    {
        id: 1,
        name: "Mouse Wireless",
        price: 250000,
        isActive: false
    },
    {
        id: 2,
        name: "Mouse Wireless",
        price: 250000,
        isActive: true
        // description tidak kita tulis, dan ini TIDAK error karena sifatnya optional (?)
    },
]

function calculateTotal(items: Product[]): number {
    let total = 0;
    items.forEach((item) => {
        if (item.isActive){
            total += item.price
        }
    });

    return total;
}

//menjalankn kode
const grandTotal = calculateTotal(cart);
console.log(grandTotal);