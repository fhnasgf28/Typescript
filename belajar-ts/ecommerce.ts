// bagian 1 definisi tipe data

//1. enum : kumpulan nilai konstan agar tidak typo
//mirip fields selection di odoo

enum OrderStatus {
    Draft = "Draft",
    Confirmed = "Confirmed",
    Paid = "Paid",
    Done = "Done",
}

//2. interface dasar
interface Product {
    id: number;
    name: string;
    price: number;
}

//3 interface turunan (extends)
//cartItem otomatis punya id, name, price dari product
//kita tinggal tambah properti khusus, yaiitu qty

interface CartItem extends Product {
    qty: number;
}

//4 UNION TYPE
type PaymentMethod = "Transfer" | "Cash" | "E-Wallet" | "qris";

//5 interface utama
interface Order {
    orderId: string;
    status: OrderStatus;
    items: CartItem[];
    payment: PaymentMethod;
    customerName?:string;
}

//Bagian 2 IMPLEMENTASI Logika

const myOrder: Order = {
    orderId: "ORD001",
    status: OrderStatus.Draft,
    payment: "Transfer",
    items: [
        {
            id: 1,
            name: "Laptop Gaming",
            price: 15000000,
            qty: 1
        },
        {
            id: 2,
            name: "Mouse",
            price: 100000,
            qty: 2
        }
    ]
}

//fungsi menghitung total

function processOrder(order: Order): void {
    let total = 0;
    console.log(`Processing Order: ${order.orderId}`);
    console.log(`Status: ${order.status}`);

    order.items.forEach((item) => {
        const subtotal = item.price * item.qty;
        console.log(`- ${item.name} x ${item.qty} = Rp ${subtotal}`)
        total += subtotal;
    })

    console.log(`Total Bayar (${order.payment}): Rp ${total}`)
}

processOrder(myOrder);