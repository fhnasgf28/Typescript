let name: string = "Farhan";
let age: number = 25;
let isDeveloper: boolean = true;

//interface
interface IUser {
    id: number;
    name: string;
    skills: string[];
    active: boolean;
}

const user: IUser = {
    id: 1,
    name: "Farhan",
    skills: ["TypeScript", "Python", "Odoo"],
    active: true,
}

console.log(user);