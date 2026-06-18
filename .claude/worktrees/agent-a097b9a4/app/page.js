import Counter from "@/component/Counter";
import styles from './page.module.css'
import targetDateTime from '@/config/dateConfig'

export default function Home() {
  return (
    <main className={styles.main}>
      <div className={styles.container}>
        <h1 className={styles.title}>COMING SOON...</h1>
        <Counter targetDate={targetDateTime} />
      </div>
    </main>
  )
}
